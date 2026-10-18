from datetime import timedelta

from django.conf import settings
from django.tasks import task
from django.utils import timezone

from ledger.outbox.consumer import record_delivery
from ledger.outbox.relay import relay_batch


@task
def relay_outbox() -> int:
    """One relay pass to the demo consumer, then schedule the next one.

    Self-rescheduling keeps the relay periodic on django.tasks without a scheduler (ADR-0005).
    Extra chains (started twice) are harmless: skip_locked keeps them from clashing.
    """
    delivered = relay_batch(record_delivery)
    relay_outbox.using(
        run_after=timezone.now() + timedelta(seconds=settings.OUTBOX_RELAY_INTERVAL_SECONDS)
    ).enqueue()
    return delivered
