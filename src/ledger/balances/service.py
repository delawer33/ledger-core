from dataclasses import dataclass

from ledger.accounts.models import Account
from ledger.balances.models import BalanceSnapshot


@dataclass(frozen=True)
class Balance:
    posted: int
    available: int


def available_from_posted(posted: int) -> int:
    """Available Balance is Posted minus active holds; there are no holds yet."""
    return posted


def get_balance(account: Account) -> Balance:
    posted = BalanceSnapshot.objects.values_list("posted_amount", flat=True).get(account=account)
    return Balance(posted=posted, available=available_from_posted(posted))
