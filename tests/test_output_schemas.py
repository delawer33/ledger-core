"""Output schemas carry no input limits: values stored beyond MAX_AMOUNT still read back (ADR-0014)."""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client

from ledger.balances.models import BalanceSnapshot
from ledger.holds.models import Hold
from ledger.limits import MAX_AMOUNT
from ledger.posting.models import Entry, Transaction
from tests.test_transfers_api import _account

pytestmark = pytest.mark.django_db

BIGINT_MAX = 2**63 - 1


def test_transaction_with_entries_above_input_limit_reads_back_exactly(client: Client) -> None:
    source, target = _account(client, "equity"), _account(client, "liability")
    tx = Transaction.objects.create(kind="transfer", source_system="legacy", key="big")
    Entry.objects.create(transaction=tx, account_id=source, amount=-(MAX_AMOUNT + 1))
    Entry.objects.create(transaction=tx, account_id=target, amount=MAX_AMOUNT + 1)
    other = Transaction.objects.create(kind="transfer", source_system="legacy", key="max")
    Entry.objects.create(transaction=other, account_id=source, amount=-BIGINT_MAX)
    Entry.objects.create(transaction=other, account_id=target, amount=BIGINT_MAX)

    first = client.get(f"/api/v1/transactions/{tx.pk}")
    second = client.get(f"/api/v1/transactions/{other.pk}")

    assert first.status_code == 200
    assert [e["amount"] for e in first.json()["entries"]] == [-(MAX_AMOUNT + 1), MAX_AMOUNT + 1]
    assert second.status_code == 200
    assert [e["amount"] for e in second.json()["entries"]] == [-BIGINT_MAX, BIGINT_MAX]


def test_balance_and_hold_with_large_values_read_without_error(client: Client) -> None:
    account = _account(client, "liability")
    BalanceSnapshot.objects.filter(account_id=account).update(posted_amount=BIGINT_MAX)
    hold = Hold.objects.create(
        account_id=account,
        amount=MAX_AMOUNT + 1,
        expires_at=datetime.now(UTC) + timedelta(days=1),
        source_system="legacy",
        key="h",
    )

    balance = client.get(f"/api/v1/accounts/{account}/balance")
    read_hold = client.get(f"/api/v1/holds/{hold.pk}")

    assert balance.status_code == 200
    assert balance.json()["posted"] == BIGINT_MAX
    assert balance.json()["available"] == BIGINT_MAX - (MAX_AMOUNT + 1)
    assert read_hold.status_code == 200
    assert read_hold.json()["amount"] == MAX_AMOUNT + 1


def _maximums(node: object) -> list[object]:
    if isinstance(node, dict):
        found = [node["maximum"]] if "maximum" in node else []
        return found + [m for child in node.values() for m in _maximums(child)]
    if isinstance(node, list):
        return [m for child in node for m in _maximums(child)]
    return []


def test_openapi_output_schemas_have_no_amount_maximum(client: Client) -> None:
    schemas = client.get("/api/v1/openapi.json").json()["components"]["schemas"]

    for name, schema in schemas.items():
        if name.endswith("Out"):
            assert MAX_AMOUNT not in _maximums(schema), name
    assert MAX_AMOUNT in _maximums(schemas["EntryIn"])
