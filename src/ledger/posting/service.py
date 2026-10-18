"""The posting engine: the only place where money moves."""

from collections.abc import Sequence
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F

from ledger.accounts.models import Account, AccountType
from ledger.balances.models import BalanceSnapshot
from ledger.balances.service import available_amount
from ledger.errors import (
    AccountNotFoundError,
    CurrencyMismatchError,
    EntriesNotZeroSumError,
    InsufficientFundsError,
    InvalidEntriesError,
    TransactionAlreadyReversedError,
    TransactionNotFoundError,
)
from ledger.outbox.models import OutboxEvent
from ledger.posting.models import Entry, Transaction, TransactionKind

# Liability and asset accounts may not go below zero Available Balance; the rest may.
NON_NEGATIVE_TYPES = frozenset({AccountType.LIABILITY, AccountType.ASSET})
MIN_ENTRIES = 2


@dataclass(frozen=True)
class EntryLine:
    account_id: int
    amount: int


def post_transaction(
    *,
    kind: str,
    lines: Sequence[EntryLine],
    source_system: str,
    key: str,
    reversal_of: Transaction | None = None,
) -> Transaction:
    _validate_shape(lines)
    with transaction.atomic():
        # Locking in ascending id order is what keeps opposite transfers deadlock-free.
        ids = sorted(line.account_id for line in lines)
        accounts = {
            account.pk: account
            for account in Account.objects.select_for_update().filter(pk__in=ids).order_by("pk")
        }
        missing = [account_id for account_id in ids if account_id not in accounts]
        if missing:
            raise AccountNotFoundError(f"account {missing[0]} does not exist")
        _validate_currency(accounts)
        _validate_funds(lines, accounts)

        posted = Transaction.objects.create(
            kind=kind, source_system=source_system, key=key, reversal_of=reversal_of
        )
        Entry.objects.bulk_create(
            Entry(transaction=posted, account_id=line.account_id, amount=line.amount)
            for line in lines
        )
        for line in lines:
            BalanceSnapshot.objects.filter(account_id=line.account_id).update(
                posted_amount=F("posted_amount") + line.amount, version=F("version") + 1
            )
        OutboxEvent.objects.create(type="transaction.posted", payload=_event_payload(posted, lines))
    return posted


def reverse_transaction(*, transaction_id: int, source_system: str, key: str) -> Transaction:
    """Post a Reversal: the original's Entries with opposite signs, through the posting engine."""
    with transaction.atomic():
        # Locking the original serialises concurrent reversals; the unique constraint backs it up.
        original = Transaction.objects.select_for_update().filter(pk=transaction_id).first()
        if original is None:
            raise TransactionNotFoundError(f"transaction {transaction_id} does not exist")
        if Transaction.objects.filter(reversal_of=original).exists():
            raise TransactionAlreadyReversedError(f"transaction {transaction_id} is reversed")
        lines = [
            EntryLine(account_id=account_id, amount=-amount)
            for account_id, amount in original.entries.order_by("pk").values_list(
                "account_id", "amount"
            )
        ]
        return post_transaction(
            kind=TransactionKind.REVERSAL,
            lines=lines,
            source_system=source_system,
            key=key,
            reversal_of=original,
        )


def _validate_shape(lines: Sequence[EntryLine]) -> None:
    if len(lines) < MIN_ENTRIES:
        raise InvalidEntriesError("a transaction needs at least two entries")
    if any(line.amount == 0 for line in lines):
        raise InvalidEntriesError("an entry amount must not be zero")
    if len({line.account_id for line in lines}) != len(lines):
        raise InvalidEntriesError("an account may appear only once in a transaction")
    total = sum(line.amount for line in lines)
    if total != 0:
        raise EntriesNotZeroSumError(f"entries sum to {total}, expected 0")


def _validate_currency(accounts: dict[int, Account]) -> None:
    currencies = {account.currency for account in accounts.values()}
    if len(currencies) > 1:
        raise CurrencyMismatchError(f"transaction mixes currencies: {sorted(currencies)}")


def _validate_funds(lines: Sequence[EntryLine], accounts: dict[int, Account]) -> None:
    constrained = {
        line.account_id: line.amount
        for line in lines
        if accounts[line.account_id].type in NON_NEGATIVE_TYPES
    }
    for account_id, amount in constrained.items():
        if available_amount(account_id) + amount < 0:
            raise InsufficientFundsError(f"account {account_id} would go below zero")


def _event_payload(posted: Transaction, lines: Sequence[EntryLine]) -> dict[str, object]:
    return {
        "transaction_id": posted.pk,
        "kind": posted.kind,
        "reversal_of": posted.reversal_of_id,
        "posted_at": posted.posted_at.isoformat(),
        "entries": [{"account_id": line.account_id, "amount": line.amount} for line in lines],
    }
