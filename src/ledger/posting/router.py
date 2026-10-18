from typing import Annotated, Literal

from django.http import HttpRequest
from ninja import Router, Schema, Status
from pydantic import Field

from ledger.api_headers import MutationHeaders
from ledger.posting import service
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
