from typing import Any

import pytest
from django.test import Client

from ledger.accounts.models import Account
from ledger.accounts.system import seed_system_accounts
from ledger.outbox.models import OutboxEvent
from ledger.posting.models import Transaction

from .test_transfers_api import (
    _account,
    _assert_snapshots_match_entries,
    _balance,
    _fund,
    _headers,
    _post,
)

pytestmark = pytest.mark.django_db


def _system(owner: str, currency: str = "RUB") -> int:
    return Account.objects.get(owner=owner, currency=currency, is_system=True).pk


def _operate(client: Client, kind: str, account_id: int, amount: int, **extra: Any) -> Any:
    body = {"kind": kind, "account_id": account_id, "amount": amount, **extra}
    return _post(client, "/api/v1/transactions", body)


def _usd_seller(client: Client, amount: int) -> int:
    seller = _account(client, "liability", "USD")
    source = _account(client, "equity", "USD")
    body = {
        "kind": "transfer",
        "entries": [
            {"account_id": source, "amount": -amount},
            {"account_id": seller, "amount": amount},
        ],
    }
    assert _post(client, "/api/v1/transactions", body).status_code == 201
    return seller


def _funded_seller(client: Client, amount: int = 1000) -> int:
    seller = _account(client)
    _fund(client, seller, amount)
    return seller


@pytest.mark.parametrize(
    ("kind", "owner"), [("fee", "system:fee"), ("payout", "system:bank_in_transit")]
)
def test_fee_and_payout_move_money_to_the_system_account(
    client: Client, kind: str, owner: str
) -> None:
    seed_system_accounts(["RUB"])
    seller = _funded_seller(client)

    response = _operate(client, kind, seller, 300)

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == kind
    assert body["entries"] == [
        {"account_id": seller, "amount": -300},
        {"account_id": _system(owner), "amount": 300},
    ]
    assert Transaction.objects.get(pk=body["id"]).kind == kind
    assert _balance(client, seller)["posted"] == 700
    assert _balance(client, _system(owner))["posted"] == 300
    assert client.get(f"/api/v1/transactions/{body['id']}").json()["kind"] == kind
    _assert_snapshots_match_entries()


def test_system_account_is_chosen_by_the_sellers_currency(client: Client) -> None:
    seed_system_accounts(["RUB", "USD"])
    seller = _usd_seller(client, 500)

    response = _operate(client, "fee", seller, 50)

    assert response.status_code == 201
    assert response.json()["entries"][1]["account_id"] == _system("system:fee", "USD")


@pytest.mark.parametrize("kind", ["fee", "payout"])
def test_overspending_available_is_rejected_and_holds_count(client: Client, kind: str) -> None:
    seed_system_accounts(["RUB"])
    seller = _funded_seller(client, 1000)
    assert (
        _post(
            client, "/api/v1/holds", {"account_id": seller, "amount": 800, "ttl_seconds": 3600}
        ).status_code
        == 201
    )

    response = _operate(client, kind, seller, 300)

    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_funds"
    assert _balance(client, seller)["posted"] == 1000


@pytest.mark.parametrize("kind", ["fee", "payout"])
def test_missing_system_account_in_the_currency_is_a_rule_error(client: Client, kind: str) -> None:
    seed_system_accounts(["RUB"])
    seller = _usd_seller(client, 500)

    response = _operate(client, kind, seller, 10)

    assert response.status_code == 422
    assert response.json()["code"] == "system_account_not_found"
    assert Transaction.objects.filter(kind=kind).count() == 0


def test_unknown_seller_account_is_404(client: Client) -> None:
    seed_system_accounts(["RUB"])

    response = _operate(client, "fee", 999_999, 10)

    assert response.status_code == 404
    assert response.json()["code"] == "account_not_found"


@pytest.mark.parametrize("amount", [0, -5])
def test_amount_must_be_positive(client: Client, amount: int) -> None:
    seed_system_accounts(["RUB"])
    seller = _funded_seller(client)

    response = _operate(client, "payout", seller, amount)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_entries"
    assert _balance(client, seller)["posted"] == 1000


def test_destination_account_in_the_request_is_forbidden(client: Client) -> None:
    seed_system_accounts(["RUB"])
    seller = _funded_seller(client)
    other = _account(client)

    response = _operate(client, "fee", seller, 10, to_account_id=other)

    assert response.status_code == 422
    assert _balance(client, other)["posted"] == 0


def test_retry_with_same_key_returns_same_reply_and_posts_once(client: Client) -> None:
    seed_system_accounts(["RUB"])
    seller = _funded_seller(client)
    body = {"kind": "payout", "account_id": seller, "amount": 200}
    headers = _headers("payout-1")

    first = _post(client, "/api/v1/transactions", body, **headers)
    second = _post(client, "/api/v1/transactions", body, **headers)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert Transaction.objects.filter(kind="payout").count() == 1
    assert _balance(client, seller)["posted"] == 800


def test_outbox_event_carries_the_kind(client: Client) -> None:
    seed_system_accounts(["RUB"])
    seller = _funded_seller(client)
    OutboxEvent.objects.all().delete()

    _operate(client, "fee", seller, 100)

    event = OutboxEvent.objects.get()
    assert event.type == "transaction.posted"
    assert event.payload["kind"] == "fee"


def test_bank_rejection_is_a_reversal_of_the_payout(client: Client) -> None:
    seed_system_accounts(["RUB"])
    seller = _funded_seller(client)
    payout = _operate(client, "payout", seller, 400).json()["id"]

    response = _post(client, f"/api/v1/transactions/{payout}/reverse", {})

    assert response.status_code == 201
    assert response.json()["kind"] == "reversal"
    assert _balance(client, seller)["posted"] == 1000
    assert _balance(client, _system("system:bank_in_transit"))["posted"] == 0
    assert client.get(f"/api/v1/transactions/{payout}").json()["reversed_by"] is not None
    _assert_snapshots_match_entries()
