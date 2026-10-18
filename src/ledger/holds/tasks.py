from django.conf import settings
from django.tasks import task

from ledger.chains.service import ensure_chain, heartbeat, is_current, schedule_next
from ledger.holds.service import expire_due_holds

CHAIN_NAME = "hold-expiry"


@task
def expire_holds(token: str | None = None) -> int:
    """Release active holds past their TTL. Safe to run twice (ADR-0005).

    Called without a token it is a one-off run (manual, tests). With a token it is a pass of the
    scheduled chain (ADR-0016): a superseded token does nothing, and the next pass is enqueued even
    if this one fails.
    """
    if token is None:
        return expire_due_holds()
    if not is_current(CHAIN_NAME, token):
        return 0
    interval = settings.HOLD_EXPIRY_INTERVAL_SECONDS
    try:
        with heartbeat(CHAIN_NAME, token):
            return expire_due_holds()
    finally:
        schedule_next(CHAIN_NAME, token, expire_holds, interval=interval)


def ensure_hold_expiry() -> bool:
    """Start the hold-expiry chain unless a live one exists; idempotent."""
    return ensure_chain(CHAIN_NAME, expire_holds, interval=settings.HOLD_EXPIRY_INTERVAL_SECONDS)
