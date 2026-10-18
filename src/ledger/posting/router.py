from typing import Annotated, Literal

from django.http import HttpRequest, HttpResponse
from ninja import Router, Schema
from pydantic import ConfigDict, Field

from ledger.api_headers import MutationHeaders
from ledger.errors import TransactionNotFoundError
from ledger.idempotency.service import StoredReply, fingerprint, run_idempotent
from ledger.posting import service
from ledger.posting.models import Transaction
from ledger.schemas import Amount

router = Router(tags=["transactions"])


class EntryBase(Schema):
    account_id: int


class EntryIn(EntryBase):
    amount: Amount


class EntryOut(EntryBase):
    # No input limit: stored amounts may exceed MAX_AMOUNT, up to bigint (ADR-0014).
    amount: int


class TransferIn(Schema):
    kind: Literal["transfer"]
    entries: Annotated[list[EntryIn], Field(max_length=100)]


class SystemTransferIn(Schema):
    """Fee or payout: the seller account and a positive amount; the core picks the System Account."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fee", "payout"]
    account_id: int
    amount: Amount


TransactionIn = Annotated[TransferIn | SystemTransferIn, Field(discriminator="kind")]


class TransactionOut(Schema):
    id: int
    kind: str
    entries: list[EntryOut]
    posted_at: str
    reversal_of: int | None = None
    reversed_by: int | None = None


@router.post("", response={201: TransactionOut})
def create_transaction(request: HttpRequest, payload: TransactionIn) -> HttpResponse:
    headers = MutationHeaders.from_request(request)

    def operation() -> StoredReply:
        if isinstance(payload, SystemTransferIn):
            posted, lines = service.post_to_system_account(
                kind=payload.kind,
                account_id=payload.account_id,
                amount=payload.amount,
                source_system=headers.source_system,
                key=headers.key,
            )
        else:
            lines = [service.EntryLine(e.account_id, e.amount) for e in payload.entries]
            posted = service.post_transaction(
                kind=payload.kind, lines=lines, source_system=headers.source_system, key=headers.key
            )
        out = TransactionOut(
            id=posted.pk,
            kind=posted.kind,
            entries=[EntryOut(account_id=e.account_id, amount=e.amount) for e in lines],
            posted_at=posted.posted_at.isoformat(),
        )
        return StoredReply(status=201, body=out.model_dump_json())

    reply = run_idempotent(
        source_system=headers.source_system,
        key=headers.key,
        request_hash=fingerprint(request.path, payload.model_dump_json()),
        operation=operation,
    )
    return HttpResponse(reply.body, status=reply.status, content_type="application/json")


@router.get("/{transaction_id}", response=TransactionOut)
def get_transaction(request: HttpRequest, transaction_id: int) -> TransactionOut:  # noqa: ARG001
    found = Transaction.objects.filter(pk=transaction_id).prefetch_related("entries").first()
    if found is None:
        raise TransactionNotFoundError(f"transaction {transaction_id} does not exist")
    return _to_out(found)


@router.post("/{transaction_id}/reverse", response={201: TransactionOut})
def reverse_transaction(request: HttpRequest, transaction_id: int) -> HttpResponse:
    headers = MutationHeaders.from_request(request)

    def operation() -> StoredReply:
        reversal = service.reverse_transaction(
            transaction_id=transaction_id, source_system=headers.source_system, key=headers.key
        )
        return StoredReply(status=201, body=_to_out(reversal).model_dump_json())

    reply = run_idempotent(
        source_system=headers.source_system,
        key=headers.key,
        request_hash=fingerprint(request.path, ""),
        operation=operation,
    )
    return HttpResponse(reply.body, status=reply.status, content_type="application/json")


def _to_out(found: Transaction) -> TransactionOut:
    # Status is never stored: "reversed" is derived from the Transaction that points at this one.
    reversed_by = Transaction.objects.filter(reversal_of=found).values_list("pk", flat=True).first()
    return TransactionOut(
        id=found.pk,
        kind=found.kind,
        entries=[
            EntryOut(account_id=entry.account_id, amount=entry.amount)
            for entry in sorted(found.entries.all(), key=lambda entry: entry.pk)
        ],
        posted_at=found.posted_at.isoformat(),
        reversal_of=found.reversal_of_id,
        reversed_by=reversed_by,
    )
