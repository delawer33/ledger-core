"""The demo consumer: records each event once, drops duplicates by event_id."""

from concurrent.futures import ThreadPoolExecutor

from django.db import connection

from ledger.outbox.contract import OutboxMessage
from ledger.outbox.models import DeliveredEvent


def record_delivery(message: OutboxMessage) -> None:
    """Record the event on a connection of its own, committed at once.

    The consumer stands for a system outside the core: its record must not be rolled back with
    the relay's transaction, or a crash before `delivered_at` would never show a duplicate.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_record, message).result()


def _record(message: OutboxMessage) -> None:
    try:
        DeliveredEvent.objects.bulk_create(
            [
                DeliveredEvent(
                    event_id=message.event_id,
                    sequence=message.sequence,
                    type=message.type,
                    payload=message.payload,
                )
            ],
            ignore_conflicts=True,
        )
    finally:
        connection.close()
