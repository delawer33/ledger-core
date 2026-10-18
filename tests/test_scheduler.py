import time
import uuid
from datetime import datetime, timedelta
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import OperationalError
from django.tasks.base import TaskResultStatus
from django.test import override_settings
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from ledger.accounts.models import AccountType
from ledger.accounts.service import create_account
from ledger.chains.models import TaskChain
from ledger.chains.service import ensure_chain, heartbeat
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


def test_pass_longer_than_grace_is_not_duplicated_but_a_dead_one_is_revived() -> None:
    ensure_scheduled()
    name = hold_tasks.CHAIN_NAME
    token = str(TaskChain.objects.get(name=name).token)
    first_lease = timezone.now() + timedelta(milliseconds=100)  # about to expire
    TaskChain.objects.filter(name=name).update(lease_until=first_lease)

    with override_settings(TASK_CHAIN_GRACE_SECONDS=0.3), heartbeat(name, token):
        deadline = time.monotonic() + 5
        while TaskChain.objects.get(name=name).lease_until == first_lease:
            assert time.monotonic() < deadline, "the heartbeat never renewed the lease"
            time.sleep(0.05)
        # just past the original lease: without the heartbeat the chain would count as dead
        probe = first_lease + timedelta(milliseconds=1)
        assert not ensure_chain(name, hold_tasks.expire_holds, interval=30, now=probe)
        assert str(TaskChain.objects.get(name=name).token) == token

    # the pass died: no more renewals, the lease runs out and the chain is revived
    later = timezone.now() + timedelta(days=1)
    assert ensure_chain(name, hold_tasks.expire_holds, interval=30, now=later)
    assert str(TaskChain.objects.get(name=name).token) != token


def test_superseded_token_is_not_renewed_by_the_heartbeat() -> None:
    ensure_scheduled()
    name = hold_tasks.CHAIN_NAME
    lease = TaskChain.objects.get(name=name).lease_until

    with override_settings(TASK_CHAIN_GRACE_SECONDS=0.3), heartbeat(name, str(uuid.uuid4())):
        time.sleep(0.6)

    assert TaskChain.objects.get(name=name).lease_until == lease


def test_scheduler_survives_a_database_error_and_enqueues_after_recovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"n": 0}

    def flaky() -> dict[str, bool]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("server closed the connection unexpectedly")
        return ensure_scheduled()

    class Stop(Exception):
        pass

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise Stop

    cmd = "ledger.management.commands.run_scheduler"
    with (
        patch(f"{cmd}.ensure_scheduled", side_effect=flaky),
        patch(f"{cmd}.time.sleep", side_effect=fake_sleep),
        caplog.at_level("ERROR"),
        pytest.raises(Stop),
    ):
        call_command("run_scheduler", stdout=StringIO())

    assert "Scheduler pass failed" in caplog.text
    assert sleeps[0] < sleeps[1] or sleeps[0] == 1.0  # backoff first, then the normal poll
    assert TaskChain.objects.count() == len(SCHEDULED)
    assert DBTaskResult.objects.count() == len(SCHEDULED)


def _running_row(token: str, *, started_at: datetime) -> DBTaskResult[Any, Any]:
    return DBTaskResult.objects.create(
        status=TaskResultStatus.RUNNING,
        task_path=hold_tasks.expire_holds.module_path,
        args_kwargs={"args": [], "kwargs": {"token": token}},
        backend_name="default",
        run_after=started_at,
        started_at=started_at,
    )


def test_running_row_of_a_reassigned_chain_is_marked_failed() -> None:
    ensure_scheduled()
    name = hold_tasks.CHAIN_NAME
    old = str(TaskChain.objects.get(name=name).token)
    stuck = _running_row(old, started_at=timezone.now())

    later = timezone.now() + timedelta(days=1)
    assert ensure_chain(name, hold_tasks.expire_holds, interval=1, now=later)

    stuck.refresh_from_db()
    assert stuck.status == TaskResultStatus.FAILED
    assert "presumed dead" in stuck.traceback
    assert stuck.finished_at is not None


def test_running_row_of_the_current_chain_is_left_alone() -> None:
    ensure_scheduled()
    name = hold_tasks.CHAIN_NAME
    current = str(TaskChain.objects.get(name=name).token)
    live = _running_row(current, started_at=timezone.now() - timedelta(days=1))

    ensure_scheduled()

    live.refresh_from_db()
    assert live.status == TaskResultStatus.RUNNING
