from collections.abc import Iterable

from ledger.accounts.models import Account, AccountType
from ledger.balances.models import BalanceSnapshot

# Owner label -> Account Type. Clearing is equity so it may go negative; bank in transit is an
# asset (money on its way to the bank); the fee account is revenue.
SYSTEM_ACCOUNTS: dict[str, str] = {
    "system:fee": AccountType.REVENUE,
    "system:bank_in_transit": AccountType.ASSET,
    "system:clearing": AccountType.EQUITY,
}


def seed_system_accounts(currencies: Iterable[str]) -> int:
    """Create missing System Accounts (with snapshots); return how many accounts were created."""
    created_count = 0
    for currency in currencies:
        for owner, account_type in SYSTEM_ACCOUNTS.items():
            account, created = Account.objects.get_or_create(
                owner=owner,
                currency=currency,
                is_system=True,
                defaults={"type": account_type},
            )
            created_count += created
            BalanceSnapshot.objects.get_or_create(account=account)
    return created_count
