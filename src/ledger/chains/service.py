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
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.tasks import Task
from django.utils import timezone

from ledger.chains.models import TaskChain

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
        if chain.lease_until > now:
            return False
        chain.token = uuid.uuid4()
        chain.lease_until = _lease_until(now, interval)
        chain.save(update_fields=["token", "lease_until"])
        task.enqueue(token=str(chain.token))
    return True


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
