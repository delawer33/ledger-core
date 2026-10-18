from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from ledger.holds.models import Hold, HoldStatus
from ledger.holds.tasks import expire_holds
from ledger.outbox.models import OutboxEvent
from ledger.posting.models import Entry, Transaction
from tests.test_transfers_api import (
    _account,
    _balance,
    _fund,
    _headers,
    _post,
    _transfer,
)

pytestmark = pytest.mark.django_db


def _place(client: Client, account_id: int, amount: int, ttl: int = 3600) -> Any:
    body = {"account_id": account_id, "amount": amount, "ttl_seconds": ttl}
    return _post(client, "/api/v1/holds", body)


def _capture(client: Client, hold_id: int, to_account_id: int, amount: int | None = None) -> Any:
    body: dict[str, Any] = {"to_account_id": to_account_id}
    if amount is not None:
        body["amount"] = amount
    return _post(client, f"/api/v1/holds/{hold_id}/capture", body)


def _release(client: Client, hold_id: int) -> Any:
    return _post(client, f"/api/v1/holds/{hold_id}/release", {})


def _funded(client: Client, amount: int = 1000) -> int:
    account_id = _account(client)
    _fund(client, account_id, amount)
    return account_id


def test_place_hold_reduces_available_not_posted(client: Client) -> None:
    account_id = _funded(client)

    response = _place(client, account_id, 300)

    assert response.status_code == 201
    assert response.json()["status"] == "active"
    balance = _balance(client, account_id)
    assert (balance["posted"], balance["available"]) == (1000, 700)


def test_hold_above_available_is_rejected(client: Client) -> None:
    account_id = _funded(client)
    assert _place(client, account_id, 800).status_code == 201

    response = _place(client, account_id, 201)

    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_funds"


def test_transfer_is_checked_against_available(client: Client) -> None:
    account_id = _funded(client)
    other = _account(client)
    _place(client, account_id, 800)

    assert _transfer(client, account_id, other, 201).status_code == 422
    assert _transfer(client, account_id, other, 200).status_code == 201


def test_full_capture_posts_transaction_and_closes_hold(client: Client) -> None:
    account_id = _funded(client)
    target = _account(client)
    hold_id = _place(client, account_id, 300).json()["id"]

    response = _capture(client, hold_id, target)

    assert response.status_code == 200
    assert response.json()["status"] == "captured"
    transaction = Transaction.objects.get(pk=response.json()["captured_transaction_id"])
    assert transaction.kind == "hold_capture"
    amounts = {e.account_id: e.amount for e in Entry.objects.filter(transaction=transaction)}
    assert amounts == {account_id: -300, target: 300}
    assert _balance(client, account_id)["available"] == 700
    assert _balance(client, target)["posted"] == 300


def test_partial_capture_frees_the_remainder(client: Client) -> None:
    account_id = _funded(client)
    target = _account(client)
    hold_id = _place(client, account_id, 300).json()["id"]

    assert _capture(client, hold_id, target, 100).status_code == 200

    balance = _balance(client, account_id)
    assert (balance["posted"], balance["available"]) == (900, 900)


def test_capture_above_hold_is_rejected(client: Client) -> None:
    account_id = _funded(client)
    target = _account(client)
    hold_id = _place(client, account_id, 300).json()["id"]

    response = _capture(client, hold_id, target, 301)

    assert response.status_code == 422
    assert response.json()["code"] == "capture_exceeds_hold"
    assert Hold.objects.get(pk=hold_id).status == HoldStatus.ACTIVE


def test_capture_to_missing_account_rolls_back_hold_closing(client: Client) -> None:
    account_id = _funded(client)
    hold_id = _place(client, account_id, 300).json()["id"]

    response = _capture(client, hold_id, 999_999)

    assert response.status_code == 404
    assert Hold.objects.get(pk=hold_id).status == HoldStatus.ACTIVE
    assert _balance(client, account_id)["available"] == 700


def test_full_capture_of_hold_covering_all_available(client: Client) -> None:
    account_id = _funded(client, 700)
    target = _account(client)
    hold_id = _place(client, account_id, 700).json()["id"]

    response = _capture(client, hold_id, target)

    assert response.status_code == 200
    assert response.json()["status"] == "captured"
    balance = _balance(client, account_id)
    assert (balance["posted"], balance["available"]) == (0, 0)
    assert _balance(client, target)["posted"] == 700


def test_partial_capture_of_hold_covering_all_available_frees_remainder(client: Client) -> None:
    account_id = _funded(client, 700)
    target = _account(client)
    hold_id = _place(client, account_id, 700).json()["id"]

    assert _capture(client, hold_id, target, 1).status_code == 200

    balance = _balance(client, account_id)
    assert (balance["posted"], balance["available"]) == (699, 699)
    assert _balance(client, target)["posted"] == 1


def test_capture_cannot_spend_other_active_holds(client: Client) -> None:
    account_id = _funded(client, 700)
    target = _account(client)
    hold_id = _place(client, account_id, 400).json()["id"]
    _place(client, account_id, 300)

    assert _capture(client, hold_id, target, 401).json()["code"] == "capture_exceeds_hold"
    assert _capture(client, hold_id, target, 400).status_code == 200

    balance = _balance(client, account_id)
    assert (balance["posted"], balance["available"]) == (300, 0)


def test_failed_capture_of_full_hold_keeps_hold_active(client: Client) -> None:
    account_id = _funded(client, 700)
    hold_id = _place(client, account_id, 700).json()["id"]

    assert _capture(client, hold_id, 999_999).status_code == 404

    assert Hold.objects.get(pk=hold_id).status == HoldStatus.ACTIVE
    assert _balance(client, account_id)["available"] == 0


def test_release_returns_amount_without_entries(client: Client) -> None:
    account_id = _funded(client)
    hold_id = _place(client, account_id, 300).json()["id"]
    entries_before = Entry.objects.count()

    response = _release(client, hold_id)

    assert response.status_code == 200
    assert response.json()["status"] == "released"
    assert _balance(client, account_id)["available"] == 1000
    assert Entry.objects.count() == entries_before


def test_closed_hold_cannot_be_captured_or_released(client: Client) -> None:
    account_id = _funded(client)
    target = _account(client)
    released = _place(client, account_id, 100).json()["id"]
    captured = _place(client, account_id, 100).json()["id"]
    _release(client, released)
    _capture(client, captured, target)

    for response in (
        _capture(client, released, target),
        _release(client, released),
        _capture(client, captured, target),
        _release(client, captured),
    ):
        assert response.status_code == 422
        assert response.json()["code"] == "hold_not_active"


def test_missing_hold_is_404(client: Client) -> None:
    assert _release(client, 999_999).status_code == 404
    assert client.get("/api/v1/holds/999999").status_code == 404


def test_repeated_place_key_replays_first_reply(client: Client) -> None:
    account_id = _funded(client)
    headers = _headers("same-key")

    body = {"account_id": account_id, "amount": 100, "ttl_seconds": 3600}
    first = _post(client, "/api/v1/holds", body, **headers)
    again = _post(client, "/api/v1/holds", body, **headers)
    assert first.status_code == again.status_code == 201
    assert again.json()["id"] == first.json()["id"]


def test_expire_task_releases_only_overdue_holds(client: Client) -> None:
    account_id = _funded(client)
    overdue = _place(client, account_id, 100).json()["id"]
    active = _place(client, account_id, 200).json()["id"]
    Hold.objects.filter(pk=overdue).update(expires_at=timezone.now() - timedelta(seconds=1))

    expire_holds.call()

    assert Hold.objects.get(pk=overdue).status == HoldStatus.EXPIRED
    assert Hold.objects.get(pk=active).status == HoldStatus.ACTIVE
    assert _balance(client, account_id)["available"] == 800


def test_hold_events_are_written_to_outbox(client: Client) -> None:
    account_id = _funded(client)
    target = _account(client)
    captured = _place(client, account_id, 100).json()["id"]
    released = _place(client, account_id, 100).json()["id"]
    _capture(client, captured, target)
    _release(client, released)

    types = list(
        OutboxEvent.objects.filter(type__startswith="hold.")
        .order_by("sequence")
        .values_list("type", flat=True)
    )
    assert types == ["hold.placed", "hold.placed", "hold.captured", "hold.released"]
    event = OutboxEvent.objects.get(type="hold.captured")
    assert event.payload["hold_id"] == captured
    assert event.payload["captured_amount"] == 100


def test_rejected_place_leaves_no_event(client: Client) -> None:
    account_id = _funded(client, 100)

    assert _place(client, account_id, 101).status_code == 422
    assert not OutboxEvent.objects.filter(type="hold.placed").exists()
