from collections.abc import Callable
from typing import Annotated

from django.http import HttpRequest, HttpResponse
from ninja import Router, Schema
from pydantic import Field, StrictInt

from ledger.api_headers import MutationHeaders
from ledger.holds import service
from ledger.holds.models import Hold
from ledger.idempotency.service import StoredReply, fingerprint, run_idempotent
from ledger.limits import MAX_TTL_SECONDS
from ledger.schemas import Amount

router = Router(tags=["holds"])

PositiveAmount = Annotated[Amount, Field(gt=0)]


class HoldIn(Schema):
    account_id: int
    amount: PositiveAmount
    ttl_seconds: Annotated[
        StrictInt,
        Field(
            gt=0,
            le=MAX_TTL_SECONDS,
            description=f"Hold lifetime in seconds, at most {MAX_TTL_SECONDS} (one year).",
        ),
    ]


class CaptureIn(Schema):
    to_account_id: int
    amount: PositiveAmount | None = None


class HoldOut(Schema):
    id: int
    account_id: int
    amount: int
    status: str
    expires_at: str
    captured_transaction_id: int | None


def _out(hold: Hold) -> HoldOut:
    return HoldOut(
        id=hold.pk,
        account_id=hold.account_id,
        amount=hold.amount,
        status=hold.status,
        expires_at=hold.expires_at.isoformat(),
        captured_transaction_id=hold.captured_transaction_id,
    )


def _idempotent(
    request: HttpRequest, payload_json: str, status: int, action: Callable[[MutationHeaders], Hold]
) -> HttpResponse:
    headers = MutationHeaders.from_request(request)

    def operation() -> StoredReply:
        return StoredReply(status=status, body=_out(action(headers)).model_dump_json())

    reply = run_idempotent(
        source_system=headers.source_system,
        key=headers.key,
        request_hash=fingerprint(request.path, payload_json),
        operation=operation,
    )
    return HttpResponse(reply.body, status=reply.status, content_type="application/json")


@router.post("", response={201: HoldOut})
def place_hold(request: HttpRequest, payload: HoldIn) -> HttpResponse:
    return _idempotent(
        request,
        payload.model_dump_json(),
        201,
        lambda h: service.place_hold(
            account_id=payload.account_id,
            amount=payload.amount,
            ttl_seconds=payload.ttl_seconds,
            source_system=h.source_system,
            key=h.key,
        ),
    )


@router.get("/{hold_id}", response=HoldOut)
def read_hold(request: HttpRequest, hold_id: int) -> HoldOut:  # noqa: ARG001
    return _out(service.get_hold(hold_id))


@router.post("/{hold_id}/capture", response=HoldOut)
def capture_hold(request: HttpRequest, hold_id: int, payload: CaptureIn) -> HttpResponse:
    return _idempotent(
        request,
        payload.model_dump_json(),
        200,
        lambda h: service.capture_hold(
            hold_id=hold_id,
            to_account_id=payload.to_account_id,
            amount=payload.amount,
            source_system=h.source_system,
            key=h.key,
        ),
    )


@router.post("/{hold_id}/release", response=HoldOut)
def release_hold(request: HttpRequest, hold_id: int) -> HttpResponse:
    return _idempotent(request, "", 200, lambda _h: service.release_hold(hold_id=hold_id))
