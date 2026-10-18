from typing import Any

from django.db import connection, transaction

from ledger.outbox.models import OutboxEvent

# Taken by every writer and held to its commit, so sequence order equals commit order (ADR-0012).
WRITE_LOCK_KEY = 0x4C45444F  # "LEDO"
# Taken by the relay for the length of a pass, so one pass runs at a time.
RELAY_LOCK_KEY = 0x4C45444D


def emit(event_type: str, payload: dict[str, Any]) -> OutboxEvent:
    """Write an Outbox Event in the caller's transaction, last, just before it commits.

    The transaction-level advisory lock makes concurrent writers queue up from here to their
    commit. The sequence is drawn by the INSERT after the lock is held, so a larger sequence can
    never become visible before a smaller one. Call it as late in the transaction as possible: the
    lock is held until the commit, and no row lock may be taken after it.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [WRITE_LOCK_KEY])
        return OutboxEvent.objects.create(type=event_type, payload=payload)
