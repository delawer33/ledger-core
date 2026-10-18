from dataclasses import dataclass

from django.http import HttpRequest

from ledger.errors import HeaderTooLongError, MissingHeaderError
from ledger.limits import MAX_HEADER_LENGTH


@dataclass(frozen=True)
class MutationHeaders:
    """Idempotency-Key and X-Source-System, required on every mutating request."""

    source_system: str
    key: str

    @classmethod
    def from_request(cls, request: HttpRequest) -> "MutationHeaders":
        source_system = request.headers.get("X-Source-System", "").strip()
        key = request.headers.get("Idempotency-Key", "").strip()
        if not source_system:
            raise MissingHeaderError("header X-Source-System is required")
        if not key:
            raise MissingHeaderError("header Idempotency-Key is required")
        if len(source_system) > MAX_HEADER_LENGTH:
            raise HeaderTooLongError(
                f"header X-Source-System is longer than {MAX_HEADER_LENGTH} characters"
            )
        if len(key) > MAX_HEADER_LENGTH:
            raise HeaderTooLongError(
                f"header Idempotency-Key is longer than {MAX_HEADER_LENGTH} characters"
            )
        return cls(source_system=source_system, key=key)
