from django.conf import settings
from django.tasks import task

from ledger.chains.service import ensure_chain, is_current, schedule_next
from ledger.outbox.consumer import record_delivery
from ledger.outbox.relay import relay_batch

CHAIN_NAME = "outbox-relay"


@task
def relay_outbox(token: str = "") -> int:
    """One relay pass to the demo consumer, then schedule the next one.

    The pass belongs to a task chain (ADR-0012): a superseded token does nothing and does not
    reschedule. A consumer error still reschedules, so one bad pass does not end delivery; a
    killed worker does not, and `ensure_outbox_relay` revives the chain once its lease expires.
    """
    if not is_current(CHAIN_NAME, token):
        return 0
    interval = settings.OUTBOX_RELAY_INTERVAL_SECONDS
    try:
        delivered = relay_batch(record_delivery)
    except Exception:
        schedule_next(CHAIN_NAME, token, relay_outbox, interval=interval)
        raise
    schedule_next(CHAIN_NAME, token, relay_outbox, interval=interval)
    return delivered


def ensure_outbox_relay() -> bool:
    """Start the relay chain unless a live one exists; idempotent, call it as often as needed."""
    return ensure_chain(CHAIN_NAME, relay_outbox, interval=settings.OUTBOX_RELAY_INTERVAL_SECONDS)
