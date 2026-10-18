"""Exactly one live chain per self-rescheduling task (ADR-0012).

A chain is a task that enqueues its own next pass. django.tasks has no scheduler and no worker
liveness, so a worker killed mid-pass would end the chain silently, and a blind second start would
add a second chain. A TaskChain row holds the chain's token and a lease:

- every pass renews the lease when it enqueues the next pass;
- `ensure_chain` starts a chain only if there is none or its lease has expired, under a row lock,
  and a new chain gets a new token;
- a task whose token is no longer current is superseded: it does its work no more and does not
  reschedule, so a presumed-dead chain that was merely slow cannot live on beside its successor.

`ensure_chain` is idempotent and safe to call from anywhere, any number of times: a worker start,
a cron line, the scheduler. It is the one recovery mechanism.

While a pass runs, `heartbeat` keeps extending the lease, so a live pass longer than the grace is
never mistaken for a dead one; a killed process stops the heartbeat and the lease runs out. When
`ensure_chain` has reassigned a chain, the RUNNING rows of its superseded passes are marked FAILED
(`fail_orphaned_runs`).
"""

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import close_old_connections, connection, transaction
from django.tasks import Task
from django.tasks.base import TaskResultStatus
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from ledger.chains.models import TaskChain

logger = logging.getLogger(__name__)

_EPOCH = datetime.min.replace(tzinfo=timezone.get_fixed_timezone(0))


def _lease_until(now: datetime, interval: float) -> datetime:
    return now + timedelta(seconds=interval + settings.TASK_CHAIN_GRACE_SECONDS)


def ensure_chain(
    name: str, task: Task[..., Any], *, interval: float, now: datetime | None = None
) -> bool:
    """Start the chain unless a live one exists; True if a new chain was started."""
    now = now or timezone.now()
    with transaction.atomic():
        TaskChain.objects.get_or_create(name=name, defaults={"lease_until": _EPOCH})
        chain = TaskChain.objects.select_for_update().get(name=name)
        started = chain.lease_until <= now
        if started:
            chain.token = uuid.uuid4()
            chain.lease_until = _lease_until(now, interval)
            chain.save(update_fields=["token", "lease_until"])
            task.enqueue(token=str(chain.token))
        current = str(chain.token)
    fail_orphaned_runs(task, current, now=now)
    return started


def fail_orphaned_runs(task: Task[..., Any], current_token: str, *, now: datetime) -> int:
    """Mark RUNNING passes of a reassigned chain FAILED; returns how many.

    A pass whose token is no longer the chain's current one and that started more than the grace
    ago belongs to a worker that was killed: the heartbeat would have kept its lease alive
    otherwise, and a superseded pass exits at once. The queue never learns of the death, so
    without this the row stays RUNNING for ever.
    """
    cutoff = now - timedelta(seconds=settings.TASK_CHAIN_GRACE_SECONDS)
    orphans = DBTaskResult.objects.filter(
        status=TaskResultStatus.RUNNING,
        task_path=task.module_path,
        started_at__lt=cutoff,
        args_kwargs__kwargs__has_key="token",
    ).exclude(args_kwargs__kwargs__token=current_token)
    reason = (
        "Worker presumed dead: the chain lease expired and the chain was reassigned "
        "(ledger.chains.service, ADR-0012)"
    )
    return orphans.update(
        status=TaskResultStatus.FAILED,
        finished_at=now,
        exception_class_path="ledger.chains.service.WorkerPresumedDead",
        traceback=reason,
    )


@contextmanager
def heartbeat(name: str, token: str) -> Iterator[None]:
    """Keep the chain's lease alive for as long as the body runs.

    A thread, on its own connection, extends the lease to now + grace every third of the grace,
    but only while `token` is still the chain's current one. The thread dies with the process, so
    a killed pass stops renewing and the lease expires as before.
    """
    stop = threading.Event()
    grace = settings.TASK_CHAIN_GRACE_SECONDS

    def beat() -> None:
        try:
            while not stop.wait(grace / 3):
                try:
                    _extend_lease(name, token, timezone.now() + timedelta(seconds=grace))
                except Exception:
                    logger.exception("Lease heartbeat of chain %s failed", name)
                    close_old_connections()
        finally:
            connection.close()

    thread = threading.Thread(target=beat, name=f"heartbeat-{name}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def _extend_lease(name: str, token: str, until: datetime) -> None:
    with transaction.atomic():
        chain = TaskChain.objects.select_for_update().filter(name=name).first()
        if chain is None or str(chain.token) != str(_as_uuid(token)) or chain.lease_until >= until:
            return
        chain.lease_until = until
        chain.save(update_fields=["lease_until"])


def is_current(name: str, token: str) -> bool:
    return TaskChain.objects.filter(name=name, token=_as_uuid(token)).exists()


def schedule_next(
    name: str, token: str, task: Task[..., Any], *, interval: float, now: datetime | None = None
) -> bool:
    """Renew the lease and enqueue the next pass; False if this task was superseded."""
    now = now or timezone.now()
    with transaction.atomic():
        chain = TaskChain.objects.select_for_update().filter(name=name).first()
        if chain is None or str(chain.token) != str(_as_uuid(token)):
            return False
        chain.lease_until = _lease_until(now, interval)
        chain.save(update_fields=["lease_until"])
        task.using(run_after=now + timedelta(seconds=interval)).enqueue(token=str(chain.token))
    return True


def _as_uuid(token: str) -> uuid.UUID:
    try:
        return uuid.UUID(token)
    except ValueError:
        return uuid.UUID(int=0)
