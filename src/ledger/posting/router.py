from typing import Annotated, Literal

from django.http import HttpRequest
from ninja import Router, Schema, Status
from pydantic import Field

from ledger.api_headers import MutationHeaders
from ledger.errors import TransactionNotFoundError
from ledger.posting import service
from ledger.posting.models import Transaction
from ledger.schemas import Amount

router = Router(tags=["transactions"])


class EntryIn(Schema):
    account_id: int
    amount: Amount


class TransactionIn(Schema):
    kind: Literal["transfer"]
    entries: Annotated[list[EntryIn], Field(max_length=100)]


class TransactionOut(Schema):
    id: int
    kind: str
    entries: list[EntryIn]
    posted_at: str
    reversal_of: int | None = None
    reversed_by: int | None = None


@router.post("", response={201: TransactionOut})
def create_transaction(request: HttpRequest, payload: TransactionIn) -> Status[TransactionOut]:
    headers = MutationHeaders.from_request(request)
    posted = service.post_transaction(
        kind=payload.kind,
        lines=[service.EntryLine(e.account_id, e.amount) for e in payload.entries],
        source_system=headers.source_system,
        key=headers.key,
    )
    return Status(
        201,
        TransactionOut(
            id=posted.pk,
            kind=posted.kind,
            entries=payload.entries,
            posted_at=posted.posted_at.isoformat(),
        ),
    )


@router.get("/{transaction_id}", response=TransactionOut)
def get_transaction(request: HttpRequest, transaction_id: int) -> TransactionOut:  # noqa: ARG001
    found = Transaction.objects.filter(pk=transaction_id).prefetch_related("entries").first()
    if found is None:
        raise TransactionNotFoundError(f"transaction {transaction_id} does not exist")
    return _to_out(found)


@router.post("/{transaction_id}/reverse", response={201: TransactionOut})
def reverse_transaction(request: HttpRequest, transaction_id: int) -> Status[TransactionOut]:
    headers = MutationHeaders.from_request(request)
    reversal = service.reverse_transaction(
        transaction_id=transaction_id, source_system=headers.source_system, key=headers.key
    )
    return Status(201, _to_out(reversal))


def _to_out(found: Transaction) -> TransactionOut:
    # Status is never stored: "reversed" is derived from the Transaction that points at this one.
    reversed_by = Transaction.objects.filter(reversal_of=found).values_list("pk", flat=True).first()
    return TransactionOut(
        id=found.pk,
        kind=found.kind,
        entries=[
            EntryIn(account_id=entry.account_id, amount=entry.amount)
            for entry in sorted(found.entries.all(), key=lambda entry: entry.pk)
        ],
        posted_at=found.posted_at.isoformat(),
        reversal_of=found.reversal_of_id,
        reversed_by=reversed_by,
    )
