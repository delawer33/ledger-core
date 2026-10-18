import json
import threading
from typing import Any

import pytest
from django.db import connection
from django.test import Client

from ledger.idempotency.models import IdempotencyRecord
from ledger.outbox.models import OutboxEvent
from ledger.posting.models import Transaction

from .test_transfers_api import _account, _fund, _headers, _transfer

pytestmark = pytest.mark.django_db


def _same(a: Any, b: Any) -> None:
    assert a.status_code == b.status_code
    assert a.content == b.content


def test_repeat_returns_the_first_response_byte_for_byte_and_posts_once(client: Client) -> None:
    a, b = _account(client, "equity"), _account(client)
    headers = _headers("k1")

    first = _transfer(client, a, b, 10, **headers)
    second = _transfer(client, a, b, 10, **headers)

    assert first.status_code == 201
    _same(first, second)
    assert Transaction.objects.count() == 1
    assert OutboxEvent.objects.count() == 1


def test_same_key_with_different_body_is_409_and_posts_nothing(client: Client) -> None:
    a, b = _account(client, "equity"), _account(client)
    headers = _headers("k1")
    assert _transfer(client, a, b, 10, **headers).status_code == 201

    response = _transfer(client, a, b, 11, **headers)

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"
    assert Transaction.objects.count() == 1


def test_repeat_of_a_rejected_request_returns_the_same_422(client: Client) -> None:
    a, b = _account(client), _account(client)
    headers = _headers("k1")

    first = _transfer(client, a, b, 10, **headers)
    _fund(client, a, 100)
    second = _transfer(client, a, b, 10, **headers)

    assert first.status_code == 422
    assert first.json()["code"] == "insufficient_funds"
    _same(first, second)
    assert Transaction.objects.filter(key="k1").count() == 0


def test_same_key_from_different_source_systems_is_independent(client: Client) -> None:
    a, b = _account(client, "equity"), _account(client)

    first = _transfer(client, a, b, 10, **{"Idempotency-Key": "k", "X-Source-System": "one"})
    second = _transfer(client, a, b, 10, **{"Idempotency-Key": "k", "X-Source-System": "two"})

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert Transaction.objects.count() == 2


def test_stored_response_is_replayed_without_recomputation(client: Client) -> None:
    """Stands in for 'the transaction was reversed since' until reversal exists."""
    a, b = _account(client, "equity"), _account(client)
    headers = _headers("k1")
    first = _transfer(client, a, b, 10, **headers)
    record = IdempotencyRecord.objects.get(source_system="storefront", key="k1")
    record.response = {"status": 201, "body": json.dumps({"stored": True})}
    record.save()

    second = _transfer(client, a, b, 10, **headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == {"stored": True}


@pytest.mark.django_db(transaction=True)
def test_parallel_requests_with_one_key_post_one_transaction() -> None:
    setup = Client()
    a, b = _account(setup, "equity"), _account(setup)
    headers = _headers("race")
    workers = 8
    barrier = threading.Barrier(workers)
    results: list[Any] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            client = Client()
            barrier.wait()
            results.append(_transfer(client, a, b, 10, **headers))
        except BaseException as exc:  # noqa: BLE001  # surfaced in the main thread
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(results) == workers
    assert {r.status_code for r in results} == {201}
    assert len({r.content for r in results}) == 1
    assert Transaction.objects.count() == 1
