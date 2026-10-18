import json
import logging
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ledger.balances.models import BalanceSnapshot
from ledger.holds.models import Hold
from ledger.reconciliation.models import Incident
from ledger.reconciliation.service import reconcile

pytestmark = pytest.mark.django_db

_counter = iter(range(1, 10_000))


def _post(client: Client, path: str, body: dict[str, Any]) -> Any:
    headers = {"Idempotency-Key": f"k-{next(_counter)}", "X-Source-System": "storefront"}
    return client.post(
        path, data=json.dumps(body), content_type="application/json", headers=headers
    )


def _account(client: Client, account_type: str = "liability") -> int:
    response = _post(
        client, "/api/v1/accounts", {"currency": "RUB", "type": account_type, "owner": "o"}
    )
    return int(response.json()["id"])


def _funded_account(client: Client, amount: int = 1000) -> int:
    source = _account(client, "equity")
    target = _account(client)
    body = {
        "kind": "transfer",
        "entries": [
            {"account_id": source, "amount": -amount},
            {"account_id": target, "amount": amount},
        ],
    }
    assert _post(client, "/api/v1/transactions", body).status_code == 201
    return target


def _balance(client: Client, account_id: int) -> int:
    return int(client.get(f"/api/v1/accounts/{account_id}/balance").json()["posted"])


def _corrupt(account_id: int, posted: int) -> None:
    BalanceSnapshot.objects.filter(account_id=account_id).update(posted_amount=posted)


def test_clean_data_opens_no_incident_and_logs_the_outcome(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    _funded_account(client)

    with caplog.at_level(logging.INFO, logger="ledger.reconciliation.service"):
        result = reconcile()

    assert result.clean
    assert result.accounts_checked == 2
    assert Incident.objects.count() == 0
    assert "reconciliation clean: 2 accounts checked" in caplog.text


def test_corrupted_snapshot_opens_incident_without_fixing_it(client: Client) -> None:
    account = _funded_account(client, 1000)
    _corrupt(account, 999)

    result = reconcile()

    incident = Incident.objects.get()
    assert (incident.account_id, incident.expected, incident.actual) == (account, 1000, 999)
    assert result.incidents_opened == [account]
    snapshot = BalanceSnapshot.objects.get(account_id=account)
    assert snapshot.posted_amount == 999
    assert not snapshot.trusted


def test_balance_is_recounted_while_incident_is_open(client: Client) -> None:
    account = _funded_account(client, 1000)
    _corrupt(account, 999)
    assert _balance(client, account) == 999

    reconcile()

    assert _balance(client, account) == 1000


def test_repeated_reconciliation_does_not_duplicate_incident(client: Client) -> None:
    account = _funded_account(client)
    _corrupt(account, 5)

    reconcile()
    second = reconcile()

    assert Incident.objects.count() == 1
    assert second.already_open == [account]
    assert second.incidents_opened == []


def test_close_requires_note_and_restores_trust(client: Client) -> None:
    account = _funded_account(client, 1000)
    _corrupt(account, 999)
    reconcile()
    incident = Incident.objects.get()

    with pytest.raises(CommandError, match="incident_note_required"):
        call_command("close_incident", str(incident.pk), note="  ")
    with pytest.raises(CommandError):
        call_command("close_incident", str(incident.pk))
    assert Incident.objects.get().closed_at is None

    _corrupt(account, 1000)  # the operator repaired the data by hand
    call_command("close_incident", str(incident.pk), note="bug found, snapshot repaired")

    incident.refresh_from_db()
    assert incident.closed_at is not None
    assert incident.note == "bug found, snapshot repaired"
    assert BalanceSnapshot.objects.get(account_id=account).trusted
    assert reconcile().clean
    assert Incident.objects.filter(closed_at__isnull=True).count() == 0


def test_closing_unknown_or_closed_incident_fails(client: Client) -> None:
    with pytest.raises(CommandError, match="incident_not_found"):
        call_command("close_incident", "9999", note="x")


def test_reconcile_command_reports_and_enqueue_uses_the_task(client: Client) -> None:
    _funded_account(client)
    out = StringIO()
    call_command("reconcile", stdout=out)
    assert "0 incidents opened" in out.getvalue()

    out = StringIO()
    call_command("reconcile", "--enqueue", stdout=out)
    assert "enqueued" in out.getvalue()


def test_holds_above_posted_are_reported(client: Client) -> None:
    account = _funded_account(client, 100)
    Hold.objects.create(
        account_id=account,
        amount=150,
        expires_at=timezone.now() + timedelta(hours=1),
        source_system="s",
        key="h",
    )

    result = reconcile()

    assert result.holds_exceeding_posted == [account]
    assert Incident.objects.count() == 0


def test_recount_is_grouped_in_the_database(client: Client) -> None:
    for _ in range(3):
        _funded_account(client)

    with CaptureQueriesContext(connection) as queries:
        reconcile()

    entry_selects = [q["sql"] for q in queries if 'FROM "ledger_entry"' in q["sql"]]
    assert len(entry_selects) == 1
    assert "SUM(" in entry_selects[0]
    assert "GROUP BY" in entry_selects[0]


def _transfer(client: Client, source: int, target: int, amount: int) -> Any:
    body = {
        "kind": "transfer",
        "entries": [
            {"account_id": source, "amount": -amount},
            {"account_id": target, "amount": amount},
        ],
    }
    return _post(client, "/api/v1/transactions", body)


def _untrusted_overstated(client: Client) -> tuple[int, int]:
    """A funded account (1000) whose snapshot was inflated by 777 and flagged by reconciliation."""
    account = _funded_account(client, 1000)
    _corrupt(account, 1777)
    reconcile()
    assert not BalanceSnapshot.objects.get(account_id=account).trusted
    return account, _account(client, "equity")


def test_untrusted_overstated_snapshot_does_not_allow_overspending(client: Client) -> None:
    account, other = _untrusted_overstated(client)

    response = _transfer(client, account, other, 1500)

    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_funds"
    assert _transfer(client, account, other, 1000).status_code == 201


def test_untrusted_snapshot_keeps_drifting_by_delta_and_stays_untrusted(client: Client) -> None:
    account, other = _untrusted_overstated(client)

    assert _transfer(client, account, other, 400).status_code == 201

    snapshot = BalanceSnapshot.objects.get(account_id=account)
    assert (snapshot.posted_amount, snapshot.trusted) == (1377, False)
    assert _balance(client, account) == 600


def test_hold_and_capture_on_untrusted_snapshot_count_from_entries(client: Client) -> None:
    account, other = _untrusted_overstated(client)

    too_big = _post(
        client, "/api/v1/holds", {"account_id": account, "amount": 1500, "ttl_seconds": 60}
    )
    assert too_big.status_code == 422
    assert too_big.json()["code"] == "insufficient_funds"

    placed = _post(
        client, "/api/v1/holds", {"account_id": account, "amount": 800, "ttl_seconds": 60}
    )
    assert placed.status_code == 201
    assert _transfer(client, account, other, 300).status_code == 422  # only 200 available
    assert _transfer(client, account, other, 200).status_code == 201


def test_trusted_snapshot_is_not_recounted_by_the_engine(client: Client) -> None:
    account = _funded_account(client, 1000)
    other = _account(client, "equity")

    with CaptureQueriesContext(connection) as queries:
        assert _transfer(client, account, other, 100).status_code == 201

    assert not [q for q in queries if "SUM(" in q["sql"] and 'FROM "ledger_entry"' in q["sql"]]


def test_negative_posted_on_a_restricted_account_opens_an_incident(client: Client) -> None:
    account = _funded_account(client, 1000)
    other = _account(client, "equity")
    # Overdraw behind the engine's back: Entries and snapshot agree, the balance is -388.
    BalanceSnapshot.objects.filter(account_id=account).update(trusted=True)
    _post_raw_overdraft(account, other, 1388)

    result = reconcile()

    incident = Incident.objects.get()
    assert (incident.account_id, incident.expected, incident.actual) == (account, -388, -388)
    assert result.incidents_opened == [account]
    assert not result.clean
    assert not BalanceSnapshot.objects.get(account_id=account).trusted
    assert reconcile().already_open == [account]


def test_negative_posted_on_an_unrestricted_account_is_fine(client: Client) -> None:
    funded = _funded_account(client, 1000)  # the equity source is at -1000

    result = reconcile()

    assert result.clean
    assert funded
    assert Incident.objects.count() == 0


def _post_raw_overdraft(account: int, other: int, amount: int) -> None:
    from ledger.posting.models import Entry, Transaction

    tx = Transaction.objects.create(kind="transfer", source_system="s", key="raw")
    Entry.objects.bulk_create(
        [
            Entry(transaction=tx, account_id=account, amount=-amount),
            Entry(transaction=tx, account_id=other, amount=amount),
        ]
    )
    BalanceSnapshot.objects.filter(account_id=account).update(posted_amount=1000 - amount)
    BalanceSnapshot.objects.filter(account_id=other).update(posted_amount=amount)
