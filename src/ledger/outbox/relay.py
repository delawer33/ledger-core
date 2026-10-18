from django.db import transaction
from django.utils import timezone

from ledger.outbox.contract import Consumer, OutboxMessage
from ledger.outbox.models import OutboxEvent

DEFAULT_BATCH_SIZE = 100


def relay_batch(consumer: Consumer, *, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Deliver up to batch_size undelivered events in sequence order; return how many.

    Rows are locked with skip_locked, so concurrent relays take disjoint events. delivered_at is
    committed together with the batch: a crash after the consumer ran but before commit leaves the
    events undelivered and they are delivered again (at-least-once). If the consumer raises, the
    whole batch is rolled back and retried on the next run.
    """
    with transaction.atomic():
        events = list(
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(delivered_at__isnull=True)
            .order_by("sequence")[:batch_size]
        )
        for event in events:
            consumer(
                OutboxMessage(
                    event_id=event.event_id,
                    sequence=event.sequence,
                    type=event.type,
                    payload=event.payload,
                    occurred_at=event.created_at,
                )
            )
        OutboxEvent.objects.filter(pk__in=[e.pk for e in events]).update(
            delivered_at=timezone.now()
        )
    return len(events)
