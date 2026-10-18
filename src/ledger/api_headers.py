from dataclasses import dataclass

from django.http import HttpRequest

from ledger.errors import MissingHeaderError


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
        return cls(source_system=source_system, key=key)
