from django.db import connection, transaction
from django.utils import timezone

from ledger.outbox.contract import Consumer, OutboxMessage
from ledger.outbox.models import OutboxEvent
from ledger.outbox.service import RELAY_LOCK_KEY

DEFAULT_BATCH_SIZE = 100


def relay_batch(consumer: Consumer, *, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Deliver up to batch_size undelivered events in sequence order; return how many.

    One pass runs at a time (a transaction-level advisory lock; a second concurrent pass returns
    0), so the consumer sees events in sequence order across batches too. Writers hold their own
    lock to commit (see `service.emit`), so no event with a smaller sequence can appear after a
    larger one was delivered. delivered_at is committed together with the batch: a crash after the
    consumer ran but before commit leaves the events undelivered and they are delivered again
    (at-least-once). If the consumer raises, the whole batch is rolled back and retried on the next
    run, before anything after it.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [RELAY_LOCK_KEY])
            if not cursor.fetchone()[0]:
                return 0
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
