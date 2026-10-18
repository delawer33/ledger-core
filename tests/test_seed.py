import pytest
from django.core.management import call_command

from ledger.accounts.models import Account
from ledger.balances.models import BalanceSnapshot


@pytest.mark.django_db
def test_seed_creates_system_accounts_and_is_idempotent() -> None:
    call_command("seed")
    first = sorted(Account.objects.values_list("owner", "currency", "type", "is_system"))

    call_command("seed")
    call_command("seed")

    assert sorted(Account.objects.values_list("owner", "currency", "type", "is_system")) == first
    assert [owner for owner, *_ in first] == [
        "system:bank_in_transit",
        "system:clearing",
        "system:fee",
    ]
    assert all(is_system for *_, is_system in first)
    assert BalanceSnapshot.objects.count() == 3
