"""Edge values at the API boundary answer 4xx with a rule code and write nothing (ADR-0014)."""

from typing import Any

import pytest
from django.test import Client

from ledger.balances.models import BalanceSnapshot
from ledger.holds.models import Hold
from ledger.idempotency.models import IdempotencyRecord
from ledger.limits import MAX_AMOUNT, MAX_TTL_SECONDS
from ledger.outbox.models import OutboxEvent
from ledger.posting.models import Entry, Transaction
from tests.test_transfers_api import _account, _balance, _fund, _headers, _post, _transfer

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("header", ["Idempotency-Key", "X-Source-System"])
def test_header_of_256_characters_is_rejected_and_leaves_no_claim(
    client: Client, header: str
) -> None:
    source, target = _account(client, "equity"), _account(client, "liability")
    claims = IdempotencyRecord.objects.count()
    headers = _headers() | {header: "k" * 256}

    response = _transfer(client, source, target, 10, **headers)

    assert response.status_code == 400
    assert response.json()["code"] == "header_too_long"
    assert IdempotencyRecord.objects.count() == claims
    assert not Transaction.objects.exists()


def test_header_of_255_characters_passes(client: Client) -> None:
    source, target = _account(client, "equity"), _account(client, "liability")
    headers = {"Idempotency-Key": "k" * 255, "X-Source-System": "s" * 255}

    assert _transfer(client, source, target, 10, **headers).status_code == 201


def test_amount_above_maximum_is_a_validation_error(client: Client) -> None:
    source, target = _account(client, "equity"), _account(client, "liability")

    response = _transfer(client, source, target, MAX_AMOUNT + 1)

    assert response.status_code == 422
    assert not Entry.objects.exists()


def test_extreme_int64_amount_is_a_validation_error(client: Client) -> None:
    source, target = _account(client, "equity"), _account(client, "liability")

    assert _transfer(client, source, target, 2**63 - 1).status_code == 422
    body = {
        "kind": "transfer",
        "entries": [{"account_id": source, "amount": 2**63}, {"account_id": target, "amount": -1}],
    }
    assert _post(client, "/api/v1/transactions", body).status_code == 422


def test_amount_exactly_maximum_passes(client: Client) -> None:
    source, target = _account(client, "equity"), _account(client, "liability")

    assert _transfer(client, source, target, MAX_AMOUNT).status_code == 201
    assert _balance(client, target)["posted"] == MAX_AMOUNT


def test_transfer_that_would_overflow_posted_balance_is_a_rule_error(client: Client) -> None:
    target = _account(client, "liability")
    for _ in range(9):
        _fund(client, target, MAX_AMOUNT)
    before = _balance(client, target)
    events = OutboxEvent.objects.count()
    source = _account(client, "equity")

    response = _transfer(client, source, target, MAX_AMOUNT)

    assert response.status_code == 422
    assert response.json()["code"] == "balance_overflow"
    assert _balance(client, target) == before
    assert BalanceSnapshot.objects.get(account_id=target).posted_amount == before["posted"]
    assert OutboxEvent.objects.count() == events


def test_overflow_error_is_stored_and_replayed_like_any_rule_error(client: Client) -> None:
    target = _account(client, "liability")
    for _ in range(9):
        _fund(client, target, MAX_AMOUNT)
    source = _account(client, "equity")
    headers = _headers()

    first = _transfer(client, source, target, MAX_AMOUNT, **headers)
    second = _transfer(client, source, target, MAX_AMOUNT, **headers)

    assert first.status_code == second.status_code == 422
    assert first.content == second.content


def test_negative_overflow_of_a_source_balance_is_a_rule_error(client: Client) -> None:
    source = _account(client, "equity")
    for _ in range(9):
        assert _transfer(client, source, _account(client, "equity"), MAX_AMOUNT).status_code == 201

    response = _transfer(client, source, _account(client, "equity"), MAX_AMOUNT)

    assert response.status_code == 422
    assert response.json()["code"] == "balance_overflow"


def test_hold_amount_above_maximum_is_rejected(client: Client) -> None:
    account = _account(client, "liability")
    body = {"account_id": account, "amount": MAX_AMOUNT + 1, "ttl_seconds": 60}

    assert _post(client, "/api/v1/holds", body).status_code == 422
    assert not Hold.objects.exists()


def test_hold_amount_exactly_maximum_passes(client: Client) -> None:
    account = _account(client, "liability")
    _fund(client, account, MAX_AMOUNT)
    body = {"account_id": account, "amount": MAX_AMOUNT, "ttl_seconds": 60}

    assert _post(client, "/api/v1/holds", body).status_code == 201


@pytest.mark.parametrize("ttl", [MAX_TTL_SECONDS + 1, 10**12, 2**63])
def test_ttl_above_maximum_is_rejected(client: Client, ttl: int) -> None:
    account = _account(client, "liability")
    _fund(client, account, 100)
    body: dict[str, Any] = {"account_id": account, "amount": 10, "ttl_seconds": ttl}

    response = _post(client, "/api/v1/holds", body)

    assert response.status_code == 422
    assert not Hold.objects.exists()


def test_ttl_exactly_maximum_passes(client: Client) -> None:
    account = _account(client, "liability")
    _fund(client, account, 100)
    body = {"account_id": account, "amount": 10, "ttl_seconds": MAX_TTL_SECONDS}

    assert _post(client, "/api/v1/holds", body).status_code == 201


def test_capture_amount_above_maximum_is_rejected(client: Client) -> None:
    account = _account(client, "liability")
    _fund(client, account, 100)
    hold = _post(
        client, "/api/v1/holds", {"account_id": account, "amount": 10, "ttl_seconds": 60}
    ).json()

    response = _post(
        client,
        f"/api/v1/holds/{hold['id']}/capture",
        {"to_account_id": _account(client), "amount": MAX_AMOUNT + 1},
    )

    assert response.status_code == 422
