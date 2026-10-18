from dataclasses import dataclass

from django.db.models import Sum

from ledger.accounts.models import Account
from ledger.balances.models import BalanceSnapshot
from ledger.holds.models import Hold, HoldStatus
from ledger.posting.models import Entry


@dataclass(frozen=True)
class Balance:
    posted: int
    available: int


def active_holds_total(account_id: int) -> int:
    total = Hold.objects.filter(account_id=account_id, status=HoldStatus.ACTIVE).aggregate(
        total=Sum("amount")
    )["total"]
    return int(total or 0)


def recount_posted(account_id: int) -> int:
    """Posted Balance from Entries, summed by the database."""
    total = Entry.objects.filter(account_id=account_id).aggregate(total=Sum("amount"))["total"]
    return int(total or 0)


def posted_amount(account_id: int) -> int:
    """Posted Balance: the snapshot while trusted, a recount of Entries while an Incident is open.

    The engine calls this under the account lock, so the recount is consistent (ADR-0003).
    """
    row = BalanceSnapshot.objects.values_list("posted_amount", "trusted").filter(
        account_id=account_id
    )
    snapshot = row.first()
    if snapshot is None or not snapshot[1]:  # no snapshot is as untrusted as a flagged one
        return recount_posted(account_id)
    return int(snapshot[0])


def available_amount(account_id: int) -> int:
    """Available Balance is Posted minus the sum of active holds."""
    return posted_amount(account_id) - active_holds_total(account_id)


def get_balance(account: Account) -> Balance:
    posted = posted_amount(account.pk)
    return Balance(posted=posted, available=posted - active_holds_total(account.pk))
