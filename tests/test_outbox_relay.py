import threading
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest
from django.db import close_old_connections, transaction
from django.test import override_settings

from ledger.accounts.models import Account, AccountType
from ledger.outbox.consumer import record_delivery
from ledger.outbox.contract import OutboxMessage
from ledger.outbox.models import DeliveredEvent, OutboxEvent
from ledger.outbox.relay import relay_batch
from ledger.outbox.tasks import relay_outbox
from ledger.posting.models import TransactionKind
from ledger.posting.service import EntryLine, post_transaction

if TYPE_CHECKING:
    import uuid


class SimulatedCrash(Exception):
    pass


def _post(key: str) -> None:
    a = Account.objects.create(currency="RUB", type=AccountType.REVENUE, owner="a")
    b = Account.objects.create(currency="RUB", type=AccountType.REVENUE, owner="b")
    post_transaction(
        kind=TransactionKind.TRANSFER,
        lines=[EntryLine(a.pk, -100), EntryLine(b.pk, 100)],
        source_system="shop",
        key=key,
    )


def _events(count: int) -> None:
    for i in range(count):
        OutboxEvent.objects.create(type="transaction.posted", payload={"n": i})


@pytest.mark.django_db
def test_relay_delivers_event_and_marks_it_delivered() -> None:
    _post("k1")

    assert relay_batch(record_delivery) == 1

    event = OutboxEvent.objects.get()
    assert event.delivered_at is not None
    delivered = DeliveredEvent.objects.get()
    assert delivered.event_id == event.event_id
    assert delivered.sequence == event.sequence
    assert relay_batch(record_delivery) == 0


@pytest.mark.django_db
def test_message_carries_the_documented_contract() -> None:
    _events(1)
    seen: list[OutboxMessage] = []

    relay_batch(seen.append)

    event = OutboxEvent.objects.get()
    assert seen == [
        OutboxMessage(
            event_id=event.event_id,
            sequence=event.sequence,
            type="transaction.posted",
            payload={"n": 0},
            occurred_at=event.created_at,
        )
    ]


@pytest.mark.django_db
def test_crash_between_delivery_and_mark_redelivers_and_consumer_drops_duplicate() -> None:
    _events(1)
    external_seen: list[uuid.UUID] = []  # an external consumer: its effects survive our rollback

    def consume_then_crash(message: OutboxMessage) -> None:
        external_seen.append(message.event_id)
        record_delivery(message)
        raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        relay_batch(consume_then_crash)
    assert OutboxEvent.objects.get().delivered_at is None

    def consume(message: OutboxMessage) -> None:
        if message.event_id in external_seen:
            record_delivery(message)  # the duplicate arrives again; it must be dropped
        external_seen.append(message.event_id)

    assert relay_batch(consume) == 1
    assert len(external_seen) == 2
    assert DeliveredEvent.objects.count() == 1
    assert OutboxEvent.objects.get().delivered_at is not None


@pytest.mark.django_db
def test_demo_consumer_drops_a_duplicate_event_id() -> None:
    _events(1)
    event = OutboxEvent.objects.get()
    message = OutboxMessage(
        event.event_id, event.sequence, event.type, event.payload, event.created_at
    )

    record_delivery(message)
    record_delivery(message)

    assert DeliveredEvent.objects.count() == 1


@pytest.mark.django_db
def test_events_are_delivered_in_sequence_order() -> None:
    _events(5)
    order: list[int] = []

    while relay_batch(lambda m: order.append(m.sequence), batch_size=2):
        pass

    assert order == sorted(order)
    assert len(order) == 5


@pytest.mark.django_db(transaction=True)
def test_parallel_relays_never_deliver_the_same_event_together() -> None:
    _events(4)
    first_started = threading.Event()
    release_first = threading.Event()
    first_seen: list[int] = []
    second_seen: list[int] = []
    errors: list[BaseException] = []

    def slow_consumer(message: OutboxMessage) -> None:
        first_seen.append(message.sequence)
        first_started.set()
        assert release_first.wait(timeout=10)

    def first_relay() -> None:
        try:
            relay_batch(slow_consumer, batch_size=2)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread below
            errors.append(exc)
        finally:
            close_old_connections()

    thread = threading.Thread(target=first_relay)
    thread.start()
    try:
        assert first_started.wait(timeout=10)
        relay_batch(lambda m: second_seen.append(m.sequence), batch_size=2)
    finally:
        release_first.set()
        thread.join(timeout=10)

    assert not errors
    assert first_seen == sorted(first_seen)
    assert second_seen
    assert not set(first_seen) & set(second_seen)
    assert OutboxEvent.objects.filter(delivered_at__isnull=True).count() == 0


@pytest.mark.django_db
def test_event_written_in_a_rolled_back_transaction_does_not_exist() -> None:
    def post_then_fail() -> None:
        with transaction.atomic():
            _post("k-rollback")
            assert OutboxEvent.objects.count() == 1
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        post_then_fail()

    assert OutboxEvent.objects.count() == 0


@pytest.fixture
def dummy_tasks() -> Iterator[None]:
    tasks = {"default": {"BACKEND": "django.tasks.backends.dummy.DummyBackend"}}
    with override_settings(TASKS=tasks, OUTBOX_RELAY_INTERVAL_SECONDS=1):
        yield


@pytest.mark.django_db
@pytest.mark.usefixtures("dummy_tasks")
def test_relay_task_delivers_and_schedules_the_next_pass() -> None:
    from django.tasks import default_task_backend

    backend: Any = default_task_backend
    _post("k-task")

    assert relay_outbox.call() == 1

    assert DeliveredEvent.objects.count() == 1
    assert len(backend.results) == 1
