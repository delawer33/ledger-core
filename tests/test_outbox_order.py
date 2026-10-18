"""Outbox ordering across commits (ADR-0012) and the documented payloads (docs/outbox.md)."""

import json
import random
import re
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.db import close_old_connections, transaction
from django.utils import timezone

from ledger.accounts.models import AccountType
from ledger.accounts.service import create_account
from ledger.holds.service import capture_hold, expire_due_holds, place_hold, release_hold
from ledger.outbox.models import OutboxEvent
from ledger.outbox.relay import relay_batch
from ledger.outbox.service import emit
from ledger.posting.models import TransactionKind
from ledger.posting.service import EntryLine, post_transaction
from tests.concurrency_support import run_threads

DOCS = Path(__file__).resolve().parent.parent / "docs" / "outbox.md"


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
def test_a_later_writer_cannot_commit_before_an_earlier_one_and_overtake_it() -> None:
    first_written = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    errors: list[BaseException] = []

    def first() -> None:
        try:
            with transaction.atomic():
                emit("t", {"n": 1})
                first_written.set()
                assert release_first.wait(timeout=10)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)
        finally:
            close_old_connections()

    def second() -> None:
        try:
            with transaction.atomic():
                emit("t", {"n": 2})
            second_done.set()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)
        finally:
            close_old_connections()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    assert first_written.wait(timeout=10)
    t2.start()
    # The second writer waits for the first one's commit instead of becoming visible before it.
    assert not second_done.wait(timeout=0.5)
    assert relay_batch(lambda m: None) == 0
    release_first.set()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors
    seen: list[int] = []
    relay_batch(lambda m: seen.append(m.payload["n"]))
    assert seen == [1, 2]


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
def test_events_arrive_in_sequence_order_while_writers_commit_in_random_timing() -> None:
    writers = 8
    per_writer = 15
    delivered: list[int] = []
    stop = threading.Event()
    relay_errors: list[BaseException] = []

    def relay_loop() -> None:
        try:
            while not stop.is_set():
                relay_batch(lambda m: delivered.append(m.sequence), batch_size=7)
            while relay_batch(lambda m: delivered.append(m.sequence), batch_size=7):
                pass
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            relay_errors.append(exc)
        finally:
            close_old_connections()

    relay = threading.Thread(target=relay_loop)
    relay.start()

    def write(index: int) -> None:
        rng = random.Random(index)
        for n in range(per_writer):
            with transaction.atomic():
                emit("t", {"n": n})
                time.sleep(rng.random() * 0.01)  # commit lands later than the insert

    try:
        run_threads(writers, write)
    finally:
        stop.set()
        relay.join(timeout=30)

    assert not relay_errors
    assert len(delivered) == writers * per_writer
    assert delivered == sorted(delivered)
    assert OutboxEvent.objects.filter(delivered_at__isnull=True).count() == 0


def _documented_fields(event_type: str) -> set[str]:
    text = DOCS.read_text(encoding="utf-8")
    section = re.search(rf"### `{re.escape(event_type)}`.*?```json\n(.*?)```", text, re.DOTALL)
    assert section, f"{event_type} is not documented in docs/outbox.md"
    return set(json.loads(section.group(1)))


def _account(owner: str, kind: AccountType = AccountType.LIABILITY) -> int:
    return create_account(currency="RUB", account_type=kind, owner=owner).pk


def _funded() -> tuple[int, int]:
    source = _account("platform", AccountType.EQUITY)
    seller = _account("seller")
    post_transaction(
        kind=TransactionKind.TRANSFER,
        lines=[EntryLine(source, -1000), EntryLine(seller, 1000)],
        source_system="t",
        key="fund",
    )
    return seller, _account("buyer")


def _events_of(event_type: str) -> list[OutboxEvent]:
    return list(OutboxEvent.objects.filter(type=event_type).order_by("sequence"))


@pytest.mark.django_db
def test_documented_payloads_match_what_is_published() -> None:
    seller, buyer = _funded()
    captured = place_hold(
        account_id=seller, amount=300, ttl_seconds=60, source_system="t", key="h1"
    )
    capture_hold(hold_id=captured.pk, to_account_id=buyer, amount=200, source_system="t", key="c1")
    released = place_hold(
        account_id=seller, amount=100, ttl_seconds=60, source_system="t", key="h2"
    )
    release_hold(hold_id=released.pk)
    place_hold(account_id=seller, amount=50, ttl_seconds=1, source_system="t", key="h3")
    assert expire_due_holds(now=timezone.now() + timedelta(seconds=5)) == 1

    published: dict[str, list[dict[str, Any]]] = {}
    for event in OutboxEvent.objects.order_by("sequence"):
        published.setdefault(event.type, []).append(event.payload)
    assert set(published) == {
        "transaction.posted",
        "hold.placed",
        "hold.captured",
        "hold.released",
    }
    for event_type, payloads in published.items():
        for payload in payloads:
            assert set(payload) == _documented_fields(event_type), event_type
