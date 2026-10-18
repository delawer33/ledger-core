"""Proofs of the concurrency guarantees against real Postgres.

Each test states an invariant and fails if it is broken. Run alone: `pytest -m concurrency`.
Load is tunable through LEDGER_CONCURRENCY_* variables (see concurrency_support.py).
"""

import json
import random
import threading
import time
from collections import Counter

import pytest
from django.db import OperationalError, transaction
from django.test import Client
from psycopg.errors import DeadlockDetected

from ledger.accounts.models import Account
from ledger.accounts.service import create_account
from ledger.balances.models import BalanceSnapshot
from ledger.balances.service import available_amount
from ledger.errors import InsufficientFundsError
from ledger.holds.models import Hold, HoldStatus
from ledger.holds.service import place_hold
from ledger.invariants import assert_invariants
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


def _unordered_transfer(first: int, second: int, *, both_locked: threading.Barrier) -> None:
    """A transfer that locks `first`, waits for the peer, then locks `second`.

    Test-only stand-in for a posting engine without lock ordering: the barrier makes the
    circular wait certain instead of probable.
    """
    with transaction.atomic():
        Account.objects.select_for_update().get(pk=first)
        both_locked.wait(timeout=10)
        Account.objects.select_for_update().get(pk=second)


def test_unordered_locking_deadlocks_which_is_why_the_engine_orders_locks() -> None:
    a, b = _funded(1_000), _funded(1_000)
    both_locked = threading.Barrier(2)
    started = time.monotonic()

    def work(index: int) -> bool:
        first, second = (a, b) if index == 0 else (b, a)
        try:
            _unordered_transfer(first, second, both_locked=both_locked)
        except OperationalError as exc:
            return isinstance(exc.__cause__, DeadlockDetected)
        return False

    deadlocked = run_threads(2, work)

    # Postgres picks one victim and lets the other finish.
    assert sorted(deadlocked) == [False, True]
    assert time.monotonic() - started < 30
    assert_invariants()


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
