from django.tasks import task

from ledger.reconciliation.service import reconcile


@task
def reconcile_task() -> dict[str, int]:
    """Periodic entry point (ADR-0005); idempotent, so at-least-once delivery is fine."""
    result = reconcile()
    return {
        "accounts_checked": result.accounts_checked,
        "incidents_opened": len(result.incidents_opened),
    }
