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


def posted_amount(account_id: int) -> int:
    return int(
        BalanceSnapshot.objects.values_list("posted_amount", flat=True).get(account_id=account_id)
    )


def available_amount(account_id: int) -> int:
    """Available Balance is Posted minus the sum of active holds."""
    return posted_amount(account_id) - active_holds_total(account_id)


def recount_posted(account_id: int) -> int:
    """Posted Balance from Entries, summed by the database."""
    total = Entry.objects.filter(account_id=account_id).aggregate(total=Sum("amount"))["total"]
    return int(total or 0)


def get_balance(account: Account) -> Balance:
    """Read from the snapshot, unless an open Incident made it untrusted (ADR-0003)."""
    posted, trusted = BalanceSnapshot.objects.values_list("posted_amount", "trusted").get(
        account=account
    )
    if not trusted:
        posted = recount_posted(account.pk)
    return Balance(posted=posted, available=posted - active_holds_total(account.pk))
