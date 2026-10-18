"""Reconciliation: recount every Posted Balance from Entries and compare with the snapshots.

A mismatch opens an Incident and marks the snapshot untrusted; it never rewrites the snapshot
(ADR-0003).
"""

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ledger.accounts.models import Account
from ledger.balances.models import BalanceSnapshot
from ledger.balances.service import recount_posted
from ledger.errors import IncidentNoteRequiredError, IncidentNotFoundError
from ledger.holds.models import Hold, HoldStatus
from ledger.posting.models import Entry
from ledger.reconciliation.models import Incident

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconciliationResult:
    accounts_checked: int
    incidents_opened: list[int] = field(default_factory=list)
    already_open: list[int] = field(default_factory=list)
    holds_exceeding_posted: list[int] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.incidents_opened or self.already_open or self.holds_exceeding_posted)


def reconcile() -> ReconciliationResult:
    """Run one full pass. Safe to repeat: an account with an open Incident gets no second one."""
    # One grouped query for everything; only suspects are re-checked under a lock below.
    recounted = dict(
        Entry.objects.values_list("account_id").annotate(total=Sum("amount")).order_by()
    )
    snapshots = list(BalanceSnapshot.objects.values_list("account_id", "posted_amount"))
    suspects = [
        account_id
        for account_id, posted in snapshots
        if int(recounted.get(account_id, 0)) != posted
    ]

    opened: list[int] = []
    already_open: list[int] = []
    for account_id in suspects:
        outcome = _confirm_and_open(account_id)
        if outcome is not None:
            (opened if outcome[1] else already_open).append(account_id)

    holds_over = _holds_exceeding_posted(
        {account_id: int(recounted.get(account_id, 0)) for account_id, _ in snapshots}
    )
    result = ReconciliationResult(
        accounts_checked=len(snapshots),
        incidents_opened=opened,
        already_open=already_open,
        holds_exceeding_posted=holds_over,
    )
    _log(result)
    return result


def _confirm_and_open(account_id: int) -> tuple[Incident, bool] | None:
    """Re-check one suspect with the account locked, so a concurrent posting is not a mismatch."""
    with transaction.atomic():
        # Same lock the posting engine takes, so snapshot and Entries are read at one moment.
        Account.objects.select_for_update().get(pk=account_id)
        snapshot = BalanceSnapshot.objects.get(account_id=account_id)
        expected = recount_posted(account_id)
        if expected == snapshot.posted_amount:
            return None
        BalanceSnapshot.objects.filter(pk=snapshot.pk).update(trusted=False)
        incident, created = Incident.objects.get_or_create(
            account_id=account_id,
            closed_at=None,
            defaults={"expected": expected, "actual": snapshot.posted_amount},
        )
        return incident, created


def _holds_exceeding_posted(posted_by_account: dict[int, int]) -> list[int]:
    held = (
        Hold.objects.filter(status=HoldStatus.ACTIVE)
        .values_list("account_id")
        .annotate(total=Sum("amount"))
    )
    return sorted(
        account_id
        for account_id, total in held.order_by()
        if int(total) > posted_by_account.get(account_id, 0)
    )


def _log(result: ReconciliationResult) -> None:
    if result.clean:
        logger.info("reconciliation clean: %d accounts checked", result.accounts_checked)
        return
    logger.error(
        "reconciliation found problems: %d accounts checked, %d incidents opened, "
        "%d already open, %d accounts with holds above Posted Balance",
        result.accounts_checked,
        len(result.incidents_opened),
        len(result.already_open),
        len(result.holds_exceeding_posted),
    )


def close_incident(incident_id: int, *, note: str) -> Incident:
    """Close by hand after investigation; the snapshot becomes trusted again."""
    if not note.strip():
        raise IncidentNoteRequiredError("closing an incident requires a note")
    with transaction.atomic():
        try:
            incident = Incident.objects.select_for_update().get(pk=incident_id, closed_at=None)
        except Incident.DoesNotExist:
            raise IncidentNotFoundError(f"no open incident {incident_id}") from None
        incident.closed_at = timezone.now()
        incident.note = note.strip()
        incident.save(update_fields=["closed_at", "note"])
        BalanceSnapshot.objects.filter(account_id=incident.account_id).update(trusted=True)
    return incident
