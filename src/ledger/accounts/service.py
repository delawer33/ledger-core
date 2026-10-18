from django.db import transaction

from ledger.accounts.models import Account
from ledger.balances.models import BalanceSnapshot
from ledger.errors import AccountNotFoundError


def create_account(*, currency: str, account_type: str, owner: str) -> Account:
    with transaction.atomic():
        account = Account.objects.create(currency=currency, type=account_type, owner=owner)
        BalanceSnapshot.objects.create(account=account)
    return account


def get_account(account_id: int) -> Account:
    try:
        return Account.objects.get(pk=account_id)
    except Account.DoesNotExist:
        raise AccountNotFoundError(f"account {account_id} does not exist") from None
