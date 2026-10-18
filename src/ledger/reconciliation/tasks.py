from django.conf import settings
from django.tasks import task

from ledger.chains.service import ensure_chain, is_current, schedule_next
from ledger.reconciliation.service import reconcile

CHAIN_NAME = "reconciliation"


@task
def reconcile_task(token: str | None = None) -> dict[str, int]:
    """Reconciliation run (ADR-0005); idempotent, so at-least-once delivery is fine.

    Called without a token it is a one-off run (`reconcile --enqueue`, tests). With a token it is
    a pass of the scheduled chain (ADR-0016): a superseded token does nothing, and the next pass
    is enqueued even if this one fails.
    """
    if token is not None and not is_current(CHAIN_NAME, token):
        return {"accounts_checked": 0, "incidents_opened": 0}
    try:
        result = reconcile()
    finally:
        if token is not None:
            schedule_next(
                CHAIN_NAME,
                token,
                reconcile_task,
                interval=settings.RECONCILIATION_INTERVAL_SECONDS,
            )
    return {
        "accounts_checked": result.accounts_checked,
        "incidents_opened": len(result.incidents_opened),
    }


def ensure_reconciliation() -> bool:
    """Start the reconciliation chain unless a live one exists; idempotent."""
    return ensure_chain(
        CHAIN_NAME, reconcile_task, interval=settings.RECONCILIATION_INTERVAL_SECONDS
    )
