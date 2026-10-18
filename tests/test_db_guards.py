import pytest
from django.db import IntegrityError, connection, transaction

from ledger.accounts.models import Account, AccountType
from ledger.holds.models import Hold
from ledger.posting.models import Entry, Transaction, TransactionKind


def _two_accounts() -> tuple[Account, Account]:
    return (
        Account.objects.create(currency="RUB", type=AccountType.LIABILITY, owner="seller-1"),
        Account.objects.create(currency="RUB", type=AccountType.REVENUE, owner="platform"),
    )


def _transaction(key: str = "k1") -> Transaction:
    return Transaction.objects.create(kind=TransactionKind.TRANSFER, source_system="shop", key=key)


def _post(a: Account, b: Account, credit: int) -> None:
    with transaction.atomic():
        tx = _transaction()
        Entry.objects.create(transaction=tx, account=a, amount=-100)
        Entry.objects.create(transaction=tx, account=b, amount=credit)


@pytest.mark.django_db(transaction=True)
def test_balanced_entries_commit() -> None:
    a, b = _two_accounts()
    with transaction.atomic():
        tx = _transaction()
        Entry.objects.create(transaction=tx, account=a, amount=-100)
        Entry.objects.create(transaction=tx, account=b, amount=100)

    assert Entry.objects.filter(transaction=tx).count() == 2


@pytest.mark.django_db(transaction=True)
def test_commit_with_nonzero_sum_fails_in_database() -> None:
    a, b = _two_accounts()
    with pytest.raises(IntegrityError, match="expected 0"):
        _post(a, b, credit=99)

    assert not Entry.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_entry_update_is_rejected() -> None:
    a, b = _two_accounts()
    with transaction.atomic():
        tx = _transaction()
        entry = Entry.objects.create(transaction=tx, account=a, amount=-100)
        Entry.objects.create(transaction=tx, account=b, amount=100)

    with pytest.raises(IntegrityError, match="immutable"), transaction.atomic():
        Entry.objects.filter(pk=entry.pk).update(amount=-50)


@pytest.mark.django_db(transaction=True)
def test_entry_delete_is_rejected() -> None:
    a, b = _two_accounts()
    with transaction.atomic():
        tx = _transaction()
        Entry.objects.create(transaction=tx, account=a, amount=-100)
        Entry.objects.create(transaction=tx, account=b, amount=100)

    with pytest.raises(IntegrityError, match="immutable"), transaction.atomic():
        Entry.objects.all().delete()


@pytest.mark.django_db
def test_idempotency_pair_is_unique() -> None:
    _transaction("dup")
    with pytest.raises(IntegrityError), transaction.atomic():
        _transaction("dup")


@pytest.mark.django_db
def test_hold_amount_must_be_positive() -> None:
    a, _ = _two_accounts()
    with pytest.raises(IntegrityError, match="hold_amount_positive"), transaction.atomic():
        Hold.objects.create(
            account=a,
            amount=0,
            expires_at="2030-01-01T00:00:00Z",
            source_system="shop",
            key="h1",
        )


@pytest.mark.django_db
def test_transaction_can_be_reversed_only_once() -> None:
    original = _transaction("orig")
    Transaction.objects.create(
        kind=TransactionKind.REVERSAL, source_system="shop", key="r1", reversal_of=original
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Transaction.objects.create(
            kind=TransactionKind.REVERSAL, source_system="shop", key="r2", reversal_of=original
        )


def test_connection_is_postgres() -> None:
    assert connection.vendor == "postgresql"
