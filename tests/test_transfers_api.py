import json
from typing import Any

import pytest
from django.db.models import Sum
from django.test import Client

from ledger.balances.models import BalanceSnapshot
from ledger.outbox.models import OutboxEvent
from ledger.posting.models import Entry, Transaction

pytestmark = pytest.mark.django_db

_counter = iter(range(1, 10_000))


def _headers(key: str | None = None) -> dict[str, str]:
    return {
        "Idempotency-Key": key or f"key-{next(_counter)}",
        "X-Source-System": "storefront",
    }


def _post(client: Client, path: str, body: dict[str, Any], **extra: str) -> Any:
    headers = extra or _headers()
    return client.post(
        path, data=json.dumps(body), content_type="application/json", headers=headers
    )


def _account(client: Client, account_type: str = "liability", currency: str = "RUB") -> int:
    response = _post(
        client, "/api/v1/accounts", {"currency": currency, "type": account_type, "owner": "o"}
    )
    assert response.status_code == 201
    return int(response.json()["id"])


def _transfer(client: Client, source: int, target: int, amount: int, **extra: str) -> Any:
    body = {
        "kind": "transfer",
        "entries": [
            {"account_id": source, "amount": -amount},
            {"account_id": target, "amount": amount},
        ],
    }
    return _post(client, "/api/v1/transactions", body, **extra)


def _balance(client: Client, account_id: int) -> dict[str, Any]:
    response = client.get(f"/api/v1/accounts/{account_id}/balance")
    assert response.status_code == 200
    return dict(response.json())


def _fund(client: Client, account_id: int, amount: int) -> None:
    """Put money on a liability account from an equity account, as the platform would."""
    source = _account(client, "equity")
    assert _transfer(client, source, account_id, amount).status_code == 201


def _assert_snapshots_match_entries() -> None:
    for snapshot in BalanceSnapshot.objects.all():
        total = Entry.objects.filter(account=snapshot.account_id).aggregate(s=Sum("amount"))["s"]
        assert snapshot.posted_amount == (total or 0)


def test_create_account_and_read_empty_balance(client: Client) -> None:
    account_id = _account(client)

    assert _balance(client, account_id) == {
        "account_id": account_id,
        "currency": "RUB",
        "posted": 0,
        "available": 0,
    }


def test_balance_of_missing_account_is_404(client: Client) -> None:
    response = client.get("/api/v1/accounts/999999/balance")

    assert response.status_code == 404
    assert response.json()["code"] == "account_not_found"


def test_transfer_moves_money_and_updates_both_snapshots(client: Client) -> None:
    seller = _account(client)
    buyer = _account(client)
    _fund(client, buyer, 1000)

    response = _transfer(client, buyer, seller, 300)

    assert response.status_code == 201
    assert _balance(client, buyer)["posted"] == 700
    assert _balance(client, seller) == {
        "account_id": seller,
        "currency": "RUB",
        "posted": 300,
        "available": 300,
    }
    transaction = Transaction.objects.get(pk=response.json()["id"])
    assert sorted(transaction.entries.values_list("account_id", "amount")) == sorted(
        [(buyer, -300), (seller, 300)]
    )
    _assert_snapshots_match_entries()


def test_entries_not_summing_to_zero_are_rejected_and_nothing_is_written(client: Client) -> None:
    a, b = _account(client, "equity"), _account(client)
    body = {
        "kind": "transfer",
        "entries": [{"account_id": a, "amount": -100}, {"account_id": b, "amount": 90}],
    }

    response = _post(client, "/api/v1/transactions", body)

    assert response.status_code == 422
    assert response.json()["code"] == "entries_not_zero_sum"
    assert Transaction.objects.count() == 0
    assert Entry.objects.count() == 0


def test_mixed_currencies_are_rejected_and_nothing_is_written(client: Client) -> None:
    rub, usd = _account(client, "equity", "RUB"), _account(client, "liability", "USD")

    response = _transfer(client, rub, usd, 100)

    assert response.status_code == 422
    assert response.json()["code"] == "currency_mismatch"
    assert Transaction.objects.count() == 0
    assert OutboxEvent.objects.count() == 0


def test_transfer_that_overdraws_a_liability_account_is_rejected(client: Client) -> None:
    seller, buyer = _account(client), _account(client)
    _fund(client, seller, 100)

    response = _transfer(client, seller, buyer, 101)

    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_funds"
    assert _balance(client, seller)["posted"] == 100
    assert _balance(client, buyer)["posted"] == 0
    _assert_snapshots_match_entries()


def test_overdraw_of_an_asset_account_is_rejected(client: Client) -> None:
    bank, seller = _account(client, "asset"), _account(client)

    response = _transfer(client, bank, seller, 1)

    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_funds"


def test_revenue_account_may_go_negative(client: Client) -> None:
    fee, seller = _account(client, "revenue"), _account(client)

    response = _transfer(client, fee, seller, 50)

    assert response.status_code == 201
    assert _balance(client, fee)["posted"] == -50
    _assert_snapshots_match_entries()


def test_transfer_with_missing_account_is_404(client: Client) -> None:
    seller = _account(client, "equity")

    response = _transfer(client, seller, 999999, 10)

    assert response.status_code == 404
    assert response.json()["code"] == "account_not_found"
    assert Transaction.objects.count() == 0


def test_single_entry_and_zero_amount_are_rejected(client: Client) -> None:
    a, b = _account(client, "equity"), _account(client)
    single = {"kind": "transfer", "entries": [{"account_id": a, "amount": 0}]}
    zero = {
        "kind": "transfer",
        "entries": [{"account_id": a, "amount": 0}, {"account_id": b, "amount": 0}],
    }

    for body in (single, zero):
        response = _post(client, "/api/v1/transactions", body)
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_entries"


def test_non_integer_amount_is_rejected(client: Client) -> None:
    a, b = _account(client, "equity"), _account(client)
    body = {
        "kind": "transfer",
        "entries": [{"account_id": a, "amount": -1.5}, {"account_id": b, "amount": 1.5}],
    }

    assert _post(client, "/api/v1/transactions", body).status_code == 422


def test_transfer_writes_outbox_event_with_sequence_and_payload(client: Client) -> None:
    a, b = _account(client, "equity"), _account(client)

    response = _transfer(client, a, b, 25)

    event = OutboxEvent.objects.get()
    assert event.type == "transaction.posted"
    assert event.sequence > 0
    assert event.delivered_at is None
    assert event.payload["transaction_id"] == response.json()["id"]
    assert event.payload["entries"] == [
        {"account_id": a, "amount": -25},
        {"account_id": b, "amount": 25},
    ]


@pytest.mark.parametrize("missing", ["Idempotency-Key", "X-Source-System"])
def test_mutations_require_idempotency_and_source_headers(client: Client, missing: str) -> None:
    headers = _headers()
    del headers[missing]

    response = _post(
        client, "/api/v1/accounts", {"currency": "RUB", "type": "asset", "owner": "o"}, **headers
    )

    assert response.status_code == 400
    assert response.json()["code"] == "missing_header"


def test_reused_idempotency_key_is_not_a_server_error(client: Client) -> None:
    a, b = _account(client, "equity"), _account(client)
    headers = _headers("same-key")
    assert _transfer(client, a, b, 1, **headers).status_code == 201

    response = _transfer(client, a, b, 1, **headers)

    assert response.status_code == 409
    assert Transaction.objects.count() == 1
