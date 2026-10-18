"""The public Outbox Event contract: what a consumer receives, documented in docs/outbox.md."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class OutboxMessage:
    event_id: uuid.UUID
    sequence: int
    type: str
    payload: dict[str, Any]
    occurred_at: datetime


# A consumer must be idempotent by event_id: the relay delivers at least once.
Consumer = Callable[[OutboxMessage], None]
