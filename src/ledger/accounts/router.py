from typing import Annotated, Literal

from django.http import HttpRequest
from ninja import Router, Schema, Status
from pydantic import Field

from ledger.accounts import service
from ledger.api_headers import MutationHeaders
from ledger.balances.service import get_balance

router = Router(tags=["accounts"])

CURRENCY_PATTERN = r"^[A-Z]{3}$"


class AccountIn(Schema):
    currency: Annotated[str, Field(pattern=CURRENCY_PATTERN)]
    type: Literal["asset", "liability", "revenue", "expense", "equity"]
    owner: Annotated[str, Field(min_length=1, max_length=255)]


class AccountOut(Schema):
    id: int
    currency: str
    type: str
    owner: str


class BalanceOut(Schema):
    account_id: int
    currency: str
    posted: int
    available: int


@router.post("", response={201: AccountOut})
def create_account(request: HttpRequest, payload: AccountIn) -> Status[AccountOut]:
    MutationHeaders.from_request(request)
    account = service.create_account(
        currency=payload.currency, account_type=payload.type, owner=payload.owner
    )
    return Status(
        201,
        AccountOut(
            id=account.pk, currency=account.currency, type=account.type, owner=account.owner
        ),
    )


@router.get("/{account_id}/balance", response=BalanceOut)
def read_balance(request: HttpRequest, account_id: int) -> BalanceOut:  # noqa: ARG001
    account = service.get_account(account_id)
    balance = get_balance(account)
    return BalanceOut(
        account_id=account.pk,
        currency=account.currency,
        posted=balance.posted,
        available=balance.available,
    )
