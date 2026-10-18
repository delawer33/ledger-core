"""Proofs of the concurrency guarantees against real Postgres.

Each test states an invariant and fails if it is broken. Run alone: `pytest -m concurrency`.
Load is tunable through LEDGER_CONCURRENCY_* variables (see concurrency_support.py).
"""

import contextlib
import json
import random
import threading
import time
from collections import Counter
from collections.abc import Sequence

import pytest
from django.db import OperationalError
from django.test import Client
from psycopg.errors import DeadlockDetected

from ledger.accounts.models import Account
from ledger.accounts.service import create_account
from ledger.balances.models import BalanceSnapshot
from ledger.balances.service import available_amount, recount_posted
from ledger.errors import InsufficientFundsError
from ledger.holds.models import Hold, HoldStatus
from ledger.holds.service import place_hold
from ledger.invariants import assert_invariants
from ledger.posting import service as posting_service
from ledger.posting.models import Transaction
from ledger.posting.service import EntryLine, post_transaction

from .concurrency_support import (
    ACCOUNTS,
    OPPOSITE_ROUNDS,
    OPS_PER_THREAD,
    REPEATERS,
    THREADS,
    run_threads,
)

pytestmark = [pytest.mark.concurrency, pytest.mark.django_db(transaction=True)]


def _funded(amount: int) -> int:
    """A liability account holding `amount`, funded from a fresh equity account."""
    source = create_account(currency="RUB", account_type="equity", owner="platform")
    account = create_account(currency="RUB", account_type="liability", owner="seller")
    _transfer(source.pk, account.pk, amount, key=f"fund-{account.pk}")
    return account.pk


def _transfer(source: int, target: int, amount: int, *, key: str) -> Transaction:
    return post_transaction(
        kind="transfer",
        lines=[EntryLine(source, -amount), EntryLine(target, amount)],
        source_system="load",
        key=key,
    )


def test_random_transfers_keep_the_ledger_consistent() -> None:
    accounts = [_funded(1_000) for _ in range(ACCOUNTS)]

    def work(index: int) -> Counter[str]:
        rng = random.Random(index)
        outcomes: Counter[str] = Counter()
        for op in range(OPS_PER_THREAD):
            source, target = rng.sample(accounts, 2)
            try:
                _transfer(source, target, rng.randint(1, 400), key=f"t{index}-{op}")
                outcomes["posted"] += 1
            except InsufficientFundsError:
                outcomes["rejected"] += 1
        return outcomes

    totals = sum(run_threads(THREADS, work), Counter[str]())

    assert totals["posted"] > 0
    report = assert_invariants()
    assert report.posted_total == 0
    assert sum(available_amount(pk) for pk in accounts) == ACCOUNTS * 1_000


def test_opposite_transfers_do_not_deadlock_and_balances_converge() -> None:
    a, b = _funded(10_000), _funded(10_000)

    def work(index: int) -> None:
        source, target = (a, b) if index == 0 else (b, a)
        for round_no in range(OPPOSITE_ROUNDS):
            _transfer(source, target, 7, key=f"opp{index}-{round_no}")

    run_threads(2, work)

    # Equal counts in both directions: every balance is back where it started.
    assert BalanceSnapshot.objects.get(account_id=a).posted_amount == 10_000
    assert BalanceSnapshot.objects.get(account_id=b).posted_amount == 10_000
    assert Transaction.objects.filter(key__startswith="opp").count() == 2 * OPPOSITE_ROUNDS
    assert_invariants()


def _hold_after_first_lock(
    monkeypatch: pytest.MonkeyPatch, *, wait: float
) -> tuple[list[int], threading.Barrier]:
    """Make the engine pause after its first row lock until the peer has its own.

    Returns the log of locked account ids. With two writers locking in opposite orders the
    pause makes the circular wait certain; with a common order the peer is stuck behind the
    first lock, the barrier times out after `wait` seconds and both transfers carry on.
    """
    first_locked = threading.Barrier(2)
    seen = threading.local()
    log: list[int] = []
    real_lock = posting_service._lock_account

    def lock_account(account_id: int) -> Account | None:
        account = real_lock(account_id)
        log.append(account_id)
        if not getattr(seen, "paused", False):
            seen.paused = True
            with contextlib.suppress(threading.BrokenBarrierError):
                first_locked.wait(timeout=wait)
        return account

    monkeypatch.setattr(posting_service, "_lock_account", lock_account)
    return log, first_locked


def _run_opposite_pair(a: int, b: int, *, tag: str) -> list[bool]:
    """Two opposite transfers on the same pair; True per thread that was a deadlock victim."""

    def work(index: int) -> bool:
        source, target = (a, b) if index == 0 else (b, a)
        try:
            _transfer(source, target, 7, key=f"pair-{tag}-{index}")
        except OperationalError as exc:
            return isinstance(exc.__cause__, DeadlockDetected)
        return False

    return run_threads(2, work)


def _without_lock_ordering(account_ids: Sequence[int]) -> list[int]:
    """Mutant of `_lock_order`: lock in the caller's order, which differs between peers."""
    return list(dict.fromkeys(account_ids))


def test_lock_ordering_is_what_prevents_deadlock_on_opposite_transfers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same load, both outcomes: engine order survives, the mutated order deadlocks."""
    a, b = _funded(1_000), _funded(1_000)
    started = time.monotonic()

    # Ordered (the real engine): the peer waits for the first lock, nobody deadlocks.
    log, _ = _hold_after_first_lock(monkeypatch, wait=1.5)
    assert _run_opposite_pair(a, b, tag="ordered") == [False, False]
    assert log == sorted(log[:2]) + sorted(log[2:])  # each transfer locked low id first
    assert_invariants()

    # Unordered (mutant): both hold their first lock, then wait for each other.
    monkeypatch.setattr(posting_service, "_lock_order", _without_lock_ordering)
    _hold_after_first_lock(monkeypatch, wait=10)
    assert sorted(_run_opposite_pair(a, b, tag="mutant")) == [
        False,
        True,
    ]  # Postgres picks one victim

    assert time.monotonic() - started < 30
    assert_invariants()


def test_engine_locks_accounts_one_row_at_a_time_in_ascending_id_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observable order: the engine's row locks arrive ascending, whatever the line order."""
    accounts = [_funded(1_000) for _ in range(4)]
    locked: list[int] = []
    real_lock = posting_service._lock_account

    def spy(account_id: int) -> Account | None:
        locked.append(account_id)
        return real_lock(account_id)

    monkeypatch.setattr(posting_service, "_lock_account", spy)
    a, b, c, d = accounts

    post_transaction(
        kind="transfer",
        lines=[EntryLine(d, -10), EntryLine(a, 5), EntryLine(c, 3), EntryLine(b, 2)],
        source_system="load",
        key="order-probe",
    )

    assert locked == sorted(accounts)


def test_repeated_idempotent_request_posts_one_transaction_with_identical_replies() -> None:
    source = create_account(currency="RUB", account_type="equity", owner="platform")
    target = create_account(currency="RUB", account_type="liability", owner="seller")
    body = json.dumps(
        {
            "kind": "transfer",
            "entries": [
                {"account_id": source.pk, "amount": -50},
                {"account_id": target.pk, "amount": 50},
            ],
        }
    )
    headers = {"Idempotency-Key": "same-key", "X-Source-System": "storefront"}

    def work(_: int) -> tuple[int, bytes]:
        response = Client().post(
            "/api/v1/transactions", data=body, content_type="application/json", headers=headers
        )
        return response.status_code, response.content

    replies = run_threads(REPEATERS, work)

    assert len(set(replies)) == 1
    assert replies[0][0] == 201
    assert Transaction.objects.filter(source_system="storefront", key="same-key").count() == 1
    assert BalanceSnapshot.objects.get(account_id=target.pk).posted_amount == 50
    assert_invariants()


def test_repeated_idempotent_account_creation_makes_one_account_with_identical_replies() -> None:
    body = json.dumps({"currency": "RUB", "type": "asset", "owner": "racer"})
    headers = {"Idempotency-Key": "same-account-key", "X-Source-System": "storefront"}

    def work(_: int) -> tuple[int, bytes]:
        response = Client().post(
            "/api/v1/accounts", data=body, content_type="application/json", headers=headers
        )
        return response.status_code, response.content

    replies = run_threads(REPEATERS, work)

    assert len(set(replies)) == 1
    assert replies[0][0] == 201
    assert Account.objects.filter(owner="racer").count() == 1
    assert BalanceSnapshot.objects.filter(account__owner="racer").count() == 1
    assert_invariants()


def test_holds_and_transfers_on_one_account_never_push_available_below_zero() -> None:
    funds = 1_000
    account = _funded(funds)
    sink = create_account(currency="RUB", account_type="liability", owner="sink").pk

    def work(index: int) -> Counter[str]:
        rng = random.Random(index)
        outcomes: Counter[str] = Counter()
        for op in range(OPS_PER_THREAD):
            amount = rng.randint(1, 120)
            try:
                if (index + op) % 2:
                    place_hold(
                        account_id=account,
                        amount=amount,
                        ttl_seconds=3_600,
                        source_system="load",
                        key=f"h{index}-{op}",
                    )
                    outcomes["held"] += amount
                else:
                    _transfer(account, sink, amount, key=f"x{index}-{op}")
                    outcomes["moved"] += amount
            except InsufficientFundsError:
                outcomes["rejected"] += 1
        return outcomes

    totals = sum(run_threads(THREADS, work), Counter[str]())

    assert totals["held"] > 0
    assert totals["moved"] > 0
    assert totals["held"] + totals["moved"] <= funds
    assert Hold.objects.filter(status=HoldStatus.ACTIVE).count() > 0
    assert available_amount(account) == funds - totals["held"] - totals["moved"] >= 0
    assert_invariants()


def test_snapshot_repair_under_concurrent_postings_equals_the_recount() -> None:
    from ledger.reconciliation.models import Incident
    from ledger.reconciliation.service import reconcile, repair_snapshot

    account = _funded(10_000)
    sink = create_account(currency="RUB", account_type="liability", owner="sink").pk
    BalanceSnapshot.objects.filter(account_id=account).update(posted_amount=12_345)
    reconcile()
    incident_id = Incident.objects.get(account_id=account).pk

    def work(index: int) -> None:
        if index == 0:
            repair_snapshot(incident_id, note="concurrent repair")
            return
        for op in range(OPS_PER_THREAD):
            _transfer(account, sink, 1, key=f"r{index}-{op}")

    run_threads(THREADS, work)

    snapshot = BalanceSnapshot.objects.get(account_id=account)
    assert snapshot.posted_amount == recount_posted(account)
    assert snapshot.posted_amount == 10_000 - (THREADS - 1) * OPS_PER_THREAD
