"""The demo consumer: records each event once, drops duplicates by event_id."""

from ledger.outbox.contract import OutboxMessage
from ledger.outbox.models import DeliveredEvent


def record_delivery(message: OutboxMessage) -> None:
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
