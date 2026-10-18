"""Idempotency protocol: claim the key, run the operation once, store the reply, replay it."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from django.db import transaction

from ledger.errors import IdempotencyKeyReusedError, LedgerError
from ledger.idempotency.models import IdempotencyRecord


@dataclass(frozen=True)
class StoredReply:
    """A serialized HTTP reply: status plus the exact body text sent to the client."""

    status: int
    body: str


def fingerprint(path: str, payload: str) -> str:
    """Hash of what the caller asked for; the same key must always ask for the same thing."""
    return hashlib.sha256(f"{path}\n{payload}".encode()).hexdigest()


def run_idempotent(
    *,
    source_system: str,
    key: str,
    request_hash: str,
    operation: Callable[[], StoredReply],
) -> StoredReply:
    """Run `operation` at most once per (source_system, key) and always return its first reply.

    The claim, the operation and the stored reply share one database transaction, so a crash
    leaves no claim behind. A concurrent duplicate blocks on the claim's unique index until the
    first call commits, then replays the stored reply. A rule violation (LedgerError) is rolled
    back, but its reply is stored and replayed like a success.
    """
    with transaction.atomic():
        IdempotencyRecord.objects.bulk_create(
            [IdempotencyRecord(source_system=source_system, key=key, request_hash=request_hash)],
            ignore_conflicts=True,
        )
        record = IdempotencyRecord.objects.select_for_update().get(
            source_system=source_system, key=key
        )
        if record.response is not None:
            if record.request_hash != request_hash:
                raise IdempotencyKeyReusedError(
                    f"idempotency key {source_system}/{key} was used with a different request"
                )
            return StoredReply(status=record.response["status"], body=record.response["body"])

        try:
            with transaction.atomic():
                reply = operation()
        except LedgerError as exc:
            reply = StoredReply(
                status=int(exc.status), body=json.dumps({"code": exc.code, "detail": exc.detail})
            )
        record.response = {"status": reply.status, "body": reply.body}
        record.save(update_fields=["response"])
        return reply
