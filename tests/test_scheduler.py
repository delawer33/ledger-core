from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from ledger.accounts.models import AccountType
from ledger.accounts.service import create_account
from ledger.chains.models import TaskChain
from ledger.chains.service import ensure_chain
from ledger.holds import tasks as hold_tasks
from ledger.holds.models import Hold, HoldStatus
from ledger.holds.service import place_hold
from ledger.outbox.models import DeliveredEvent, OutboxEvent
from ledger.posting.models import TransactionKind
from ledger.posting.service import EntryLine, post_transaction
from ledger.reconciliation.service import reconcile
from ledger.scheduler import SCHEDULED, ensure_scheduled

pytestmark = pytest.mark.django_db(transaction=True)


def _run_worker() -> None:
    call_command("db_worker", "--batch", "--no-startup-delay", verbosity=0)


def _run_scheduler_once() -> str:
    out = StringIO()
    call_command("run_scheduler", "--once", stdout=out)
    return out.getvalue()


def _overdue_hold() -> Hold:
    source = create_account(currency="RUB", account_type=AccountType.REVENUE, owner="src")
    target = create_account(currency="RUB", account_type=AccountType.REVENUE, owner="dst")
    post_transaction(
        kind=TransactionKind.TRANSFER,
        lines=[EntryLine(target.pk, -1000), EntryLine(source.pk, 1000)],
        source_system="shop",
        key="fund",
    )
    hold = place_hold(
        account_id=source.pk, amount=100, ttl_seconds=60, source_system="shop", key="hold"
    )
    Hold.objects.filter(pk=hold.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    return hold


def test_scheduler_and_worker_expire_a_hold_without_a_manual_call() -> None:
    hold = _overdue_hold()

    _run_scheduler_once()
    _run_worker()

    assert Hold.objects.get(pk=hold.pk).status == HoldStatus.EXPIRED


def test_scheduler_and_worker_deliver_outbox_events() -> None:
    OutboxEvent.objects.create(type="transaction.posted", payload={})

    _run_scheduler_once()
    _run_worker()

    assert DeliveredEvent.objects.count() == 1


def test_reconciliation_runs_on_schedule() -> None:
    with patch("ledger.reconciliation.tasks.reconcile", wraps=reconcile) as spy:
        _run_scheduler_once()
        _run_worker()

    assert spy.call_count == 1


def test_restart_and_second_instance_do_not_enqueue_twice() -> None:
    first = _run_scheduler_once()
    queued = DBTaskResult.objects.count()

    again = _run_scheduler_once()  # a restart, or a second instance, within the interval
    ensure_scheduled()

    assert queued == len(SCHEDULED)
    assert "Started" in first
    assert again == ""
    assert DBTaskResult.objects.count() == queued
    assert TaskChain.objects.count() == len(SCHEDULED)


@pytest.mark.parametrize("name", list(SCHEDULED))
def test_dead_chain_is_revived_after_its_lease_expires(name: str) -> None:
    ensure_scheduled()
    old = str(TaskChain.objects.get(name=name).token)
    later = timezone.now() + timedelta(days=1)

    with patch("ledger.chains.service.timezone.now", return_value=later):
        assert ensure_scheduled()[name] is True
    assert str(TaskChain.objects.get(name=name).token) != old


def test_superseded_hold_expiry_pass_does_nothing() -> None:
    hold = _overdue_hold()
    ensure_scheduled()
    old = str(TaskChain.objects.get(name=hold_tasks.CHAIN_NAME).token)
    later = timezone.now() + timedelta(seconds=settings.TASK_CHAIN_GRACE_SECONDS + 3600)
    assert ensure_chain(hold_tasks.CHAIN_NAME, hold_tasks.expire_holds, interval=1, now=later)

    assert hold_tasks.expire_holds.call(token=old) == 0

    assert Hold.objects.get(pk=hold.pk).status == HoldStatus.ACTIVE
