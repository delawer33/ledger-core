from django.tasks import task

from ledger.holds.service import expire_due_holds


@task
def expire_holds() -> int:
    """Periodic: release active holds past their TTL. Safe to run twice (ADR-0005)."""
    return expire_due_holds()
