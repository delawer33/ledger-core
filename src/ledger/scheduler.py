"""The built-in scheduler: keeps one live chain per periodic task (ADR-0016).

There is no clock process that enqueues on a timetable. Each periodic task is a self-rescheduling
chain (ADR-0012), and the scheduler only calls `ensure_chain` for each of them. That call starts a
chain that is missing or whose lease has expired, and does nothing otherwise, so two scheduler
instances, a restart, or a cron line next to the scheduler cannot enqueue a task twice.
"""

from collections.abc import Callable

from ledger.holds.tasks import CHAIN_NAME as HOLD_EXPIRY
from ledger.holds.tasks import ensure_hold_expiry
from ledger.outbox.tasks import CHAIN_NAME as OUTBOX_RELAY
from ledger.outbox.tasks import ensure_outbox_relay
from ledger.reconciliation.tasks import CHAIN_NAME as RECONCILIATION
from ledger.reconciliation.tasks import ensure_reconciliation

SCHEDULED: dict[str, Callable[[], bool]] = {
    HOLD_EXPIRY: ensure_hold_expiry,
    RECONCILIATION: ensure_reconciliation,
    OUTBOX_RELAY: ensure_outbox_relay,
}


def ensure_scheduled() -> dict[str, bool]:
    """Ensure every scheduled chain; maps chain name to True if this call started it."""
    return {name: ensure() for name, ensure in SCHEDULED.items()}
