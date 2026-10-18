"""HTTP API: django-ninja under /api/v1."""

from django.db import DatabaseError, connection
from django.http import HttpRequest
from ninja import NinjaAPI, Schema, Status

api = NinjaAPI(title="Ledger Core", version="1", urls_namespace="ledger")


class HealthOut(Schema):
    status: str
    database: str


@api.get("/health", response={200: HealthOut, 503: HealthOut}, tags=["health"])
def health(request: HttpRequest) -> Status[HealthOut]:  # noqa: ARG001
    """Liveness plus a real round-trip to the database."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return Status(503, HealthOut(status="error", database="unavailable"))
    return Status(200, HealthOut(status="ok", database="ok"))
