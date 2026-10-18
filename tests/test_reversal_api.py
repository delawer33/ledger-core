from typing import Any

import pytest
from django.test import Client

from ledger.outbox.models import OutboxEvent
from ledger.posting.models import Entry, Transaction

from .test_transfers_api import (
    _account,
    _assert_snapshots_match_entries,
    _balance,
    _fund,
    _headers,
    _post,
    _transfer,
)

pytestmark = pytest.mark.django_db


def _reverse(client: Client, transaction_id: int, **extra: str) -> Any:
    return _post(client, f"/api/v1/transactions/{transaction_id}/reverse", {}, **extra)


def _transfer_between_fresh_accounts(client: Client, amount: int = 300) -> tuple[int, int, int]:
    seller, buyer = _account(client), _account(client)
    _fund(client, buyer, 1000)
    return buyer, seller, int(_transfer(client, buyer, seller, amount).json()["id"])


def test_reversal_mirrors_entries_and_restores_balances(client: Client) -> None:
    buyer, seller, original = _transfer_between_fresh_accounts(client)

    response = _reverse(client, original)

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "reversal"
    assert body["reversal_of"] == original
    assert sorted((e["account_id"], e["amount"]) for e in body["entries"]) == sorted(
        [(buyer, 300), (seller, -300)]
    )
    assert _balance(client, buyer)["posted"] == 1000
    assert _balance(client, seller)["posted"] == 0
    _assert_snapshots_match_entries()


def test_get_shows_reversed_by_on_original_and_reversal_of_on_reversal(client: Client) -> None:
    _, _, original = _transfer_between_fresh_accounts(client)
    assert client.get(f"/api/v1/transactions/{original}").json()["reversed_by"] is None

    reversal = _reverse(client, original).json()["id"]

    first = client.get(f"/api/v1/transactions/{original}").json()
    second = client.get(f"/api/v1/transactions/{reversal}").json()
    assert first["reversed_by"] == reversal
    assert first["reversal_of"] is None
    assert second["reversal_of"] == original
    assert second["reversed_by"] is None
    assert len(first["entries"]) == 2


def test_get_missing_transaction_is_404(client: Client) -> None:
    response = client.get("/api/v1/transactions/999999")

    assert response.status_code == 404
    assert response.json()["code"] == "transaction_not_found"


def test_reversing_a_missing_transaction_is_404(client: Client) -> None:
    response = _reverse(client, 999999)

    assert response.status_code == 404
    assert response.json()["code"] == "transaction_not_found"


def test_second_reversal_is_rejected_with_rule_code(client: Client) -> None:
    _, _, original = _transfer_between_fresh_accounts(client)
    assert _reverse(client, original).status_code == 201
    count = Transaction.objects.count()

    response = _reverse(client, original)

    assert response.status_code == 422
    assert response.json()["code"] == "transaction_already_reversed"
    assert Transaction.objects.count() == count


def test_reversal_that_overdraws_a_spent_account_is_rejected_and_nothing_is_written(
    client: Client,
) -> None:
    buyer, seller, original = _transfer_between_fresh_accounts(client)
    third = _account(client)
    assert _transfer(client, seller, third, 250).status_code == 201
    transactions, entries, events = (
        Transaction.objects.count(),
        Entry.objects.count(),
        OutboxEvent.objects.count(),
    )

    response = _reverse(client, original)

    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_funds"
    assert (Transaction.objects.count(), Entry.objects.count(), OutboxEvent.objects.count()) == (
        transactions,
        entries,
        events,
    )
    assert _balance(client, seller)["posted"] == 50
    assert _balance(client, buyer)["posted"] == 700
    _assert_snapshots_match_entries()


def test_reversal_writes_posted_event_with_kind_reversal(client: Client) -> None:
    _, _, original = _transfer_between_fresh_accounts(client)

    reversal = _reverse(client, original).json()["id"]

    event = OutboxEvent.objects.latest("sequence")
    assert event.type == "transaction.posted"
    assert event.payload["kind"] == "reversal"
    assert event.payload["transaction_id"] == reversal
    assert event.payload["reversal_of"] == original


def test_reversal_requires_idempotency_headers(client: Client) -> None:
    _, _, original = _transfer_between_fresh_accounts(client)
    headers = _headers()
    del headers["Idempotency-Key"]

    response = _reverse(client, original, **headers)

    assert response.status_code == 400
    assert response.json()["code"] == "missing_header"


def test_reversal_reuses_the_idempotency_key_guard(client: Client) -> None:
    _, _, original = _transfer_between_fresh_accounts(client)
    _, _, other = _transfer_between_fresh_accounts(client)
    headers = _headers("rev-key")
    assert _reverse(client, original, **headers).status_code == 201

    response = _reverse(client, other, **headers)

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"
