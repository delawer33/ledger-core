import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import close_old_connections, transaction
from django.tasks import default_task_backend
from django.test import override_settings
from django.utils import timezone

from ledger.accounts.models import Account, AccountType
from ledger.chains.models import TaskChain
from ledger.chains.service import ensure_chain
from ledger.outbox.consumer import record_delivery
from ledger.outbox.contract import OutboxMessage
from ledger.outbox.models import DeliveredEvent, OutboxEvent
from ledger.outbox.relay import relay_batch
from ledger.outbox.tasks import CHAIN_NAME, ensure_outbox_relay, relay_outbox
from ledger.posting.models import TransactionKind
from ledger.posting.service import EntryLine, post_transaction

if TYPE_CHECKING:
    import uuid


class SimulatedCrash(Exception):
    pass


def _post(key: str) -> None:
    a = Account.objects.create(currency="RUB", type=AccountType.REVENUE, owner="a")
    b = Account.objects.create(currency="RUB", type=AccountType.REVENUE, owner="b")
    post_transaction(
        kind=TransactionKind.TRANSFER,
        lines=[EntryLine(a.pk, -100), EntryLine(b.pk, 100)],
        source_system="shop",
        key=key,
    )


def _events(count: int) -> None:
    for i in range(count):
        OutboxEvent.objects.create(type="transaction.posted", payload={"n": i})


@pytest.mark.django_db(transaction=True)
def test_relay_delivers_event_and_marks_it_delivered() -> None:
    _post("k1")

    assert relay_batch(record_delivery) == 1

    event = OutboxEvent.objects.get()
    assert event.delivered_at is not None
    delivered = DeliveredEvent.objects.get()
    assert delivered.event_id == event.event_id
    assert delivered.sequence == event.sequence
    assert relay_batch(record_delivery) == 0


@pytest.mark.django_db
def test_message_carries_the_documented_contract() -> None:
    _events(1)
    seen: list[OutboxMessage] = []

    relay_batch(seen.append)

    event = OutboxEvent.objects.get()
    assert seen == [
        OutboxMessage(
            event_id=event.event_id,
            sequence=event.sequence,
            type="transaction.posted",
            payload={"n": 0},
            occurred_at=event.created_at,
        )
    ]


@pytest.mark.django_db(transaction=True)
def test_crash_between_delivery_and_mark_redelivers_and_consumer_drops_duplicate() -> None:
    _events(1)
    attempts: list[uuid.UUID] = []

    def consume_then_crash(message: OutboxMessage) -> None:
        attempts.append(message.event_id)
        record_delivery(message)  # the consumer's record is committed on its own connection
        raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        relay_batch(consume_then_crash)
    assert OutboxEvent.objects.get().delivered_at is None
    assert DeliveredEvent.objects.count() == 1  # delivered, but the relay never marked it

    def consume_again(message: OutboxMessage) -> None:
        attempts.append(message.event_id)
        record_delivery(message)  # the duplicate arrives again and must be dropped

    assert relay_batch(consume_again) == 1
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert DeliveredEvent.objects.count() == 1
    assert OutboxEvent.objects.get().delivered_at is not None


@pytest.mark.django_db(transaction=True)
def test_demo_consumer_drops_a_duplicate_event_id() -> None:
    _events(1)
    event = OutboxEvent.objects.get()
    message = OutboxMessage(
        event.event_id, event.sequence, event.type, event.payload, event.created_at
    )

    record_delivery(message)
    record_delivery(message)

    assert DeliveredEvent.objects.count() == 1


@pytest.mark.django_db
def test_events_are_delivered_in_sequence_order() -> None:
    _events(5)
    order: list[int] = []

    while relay_batch(lambda m: order.append(m.sequence), batch_size=2):
        pass

    assert order == sorted(order)
    assert len(order) == 5


@pytest.mark.django_db(transaction=True)
def test_a_second_relay_pass_waits_out_the_first_so_order_holds_across_batches() -> None:
    _events(4)
    first_started = threading.Event()
    release_first = threading.Event()
    first_seen: list[int] = []
    errors: list[BaseException] = []

    def slow_consumer(message: OutboxMessage) -> None:
        first_seen.append(message.sequence)
        first_started.set()
        assert release_first.wait(timeout=10)

    def first_relay() -> None:
        try:
            relay_batch(slow_consumer, batch_size=2)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread below
            errors.append(exc)
        finally:
            close_old_connections()

    thread = threading.Thread(target=first_relay)
    thread.start()
    try:
        assert first_started.wait(timeout=10)
        second_seen: list[int] = []
        assert relay_batch(lambda m: second_seen.append(m.sequence), batch_size=2) == 0
        assert second_seen == []
    finally:
        release_first.set()
        thread.join(timeout=10)

    assert not errors
    rest: list[int] = []
    assert relay_batch(lambda m: rest.append(m.sequence), batch_size=2) == 2
    assert first_seen + rest == sorted(first_seen + rest)
    assert OutboxEvent.objects.filter(delivered_at__isnull=True).count() == 0


@pytest.mark.django_db
def test_event_written_in_a_rolled_back_transaction_does_not_exist() -> None:
    def post_then_fail() -> None:
        with transaction.atomic():
            _post("k-rollback")
            assert OutboxEvent.objects.count() == 1
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        post_then_fail()

    assert OutboxEvent.objects.count() == 0


@pytest.fixture
def dummy_tasks() -> Iterator[None]:
    tasks = {"default": {"BACKEND": "django.tasks.backends.dummy.DummyBackend"}}
    with override_settings(TASKS=tasks, OUTBOX_RELAY_INTERVAL_SECONDS=1):
        yield


class WorkerKilled(BaseException):
    pass


def _token() -> str:
    return str(TaskChain.objects.get(name=CHAIN_NAME).token)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("dummy_tasks")
def test_relay_task_delivers_and_schedules_the_next_pass() -> None:
    backend: Any = default_task_backend
    _post("k-task")
    assert ensure_outbox_relay()

    assert relay_outbox.call(token=_token()) == 1

    assert DeliveredEvent.objects.count() == 1
    assert len(backend.results) == 2  # the first pass and the next one


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("dummy_tasks")
def test_double_start_does_not_create_a_second_chain() -> None:
    backend: Any = default_task_backend

    assert ensure_outbox_relay() is True
    assert ensure_outbox_relay() is False

    assert len(backend.results) == 1
    assert TaskChain.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("dummy_tasks")
def test_chain_revives_after_a_worker_killed_mid_pass() -> None:
    backend: Any = default_task_backend
    _events(3)
    assert ensure_outbox_relay()
    token = _token()

    def die_after_delivery(message: OutboxMessage) -> None:
        record_delivery(message)
        raise WorkerKilled

    with (
        patch("ledger.outbox.tasks.record_delivery", die_after_delivery),
        pytest.raises(WorkerKilled),
    ):
        relay_outbox.call(token=token)
    assert len(backend.results) == 1  # the dead pass scheduled nothing
    assert OutboxEvent.objects.filter(delivered_at__isnull=True).count() == 3

    # While the lease is alive the chain is not restarted...
    assert ensure_outbox_relay() is False
    # ...and once it expires, a restart needs no human: the same call revives it.
    later = timezone.now() + timedelta(seconds=1 + settings.TASK_CHAIN_GRACE_SECONDS + 1)
    assert ensure_chain(CHAIN_NAME, relay_outbox, interval=1, now=later) is True
    assert len(backend.results) == 2
    assert _token() != token

    assert relay_outbox.call(token=_token()) == 3
    assert DeliveredEvent.objects.count() == 3  # redelivered after the crash, deduplicated
    assert OutboxEvent.objects.filter(delivered_at__isnull=True).count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("dummy_tasks")
def test_superseded_pass_neither_delivers_nor_reschedules() -> None:
    backend: Any = default_task_backend
    _events(1)
    assert ensure_outbox_relay()
    old = _token()
    later = timezone.now() + timedelta(seconds=1 + settings.TASK_CHAIN_GRACE_SECONDS + 1)
    assert ensure_chain(CHAIN_NAME, relay_outbox, interval=1, now=later)
    scheduled = len(backend.results)

    assert relay_outbox.call(token=old) == 0

    assert len(backend.results) == scheduled
    assert OutboxEvent.objects.filter(delivered_at__isnull=True).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("dummy_tasks")
def test_consumer_error_keeps_the_chain_alive() -> None:
    backend: Any = default_task_backend
    _events(1)
    assert ensure_outbox_relay()

    def broken(message: OutboxMessage) -> None:
        raise SimulatedCrash

    with patch("ledger.outbox.tasks.record_delivery", broken), pytest.raises(SimulatedCrash):
        relay_outbox.call(token=_token())

    assert len(backend.results) == 2
