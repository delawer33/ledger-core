"""HTTP API: django-ninja under /api/v1."""

from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI, Schema, Status

from ledger.accounts.router import router as accounts_router
from ledger.errors import LedgerError
from ledger.holds.router import router as holds_router
from ledger.posting.router import router as transactions_router

api = NinjaAPI(title="Ledger Core", version="1", urls_namespace="ledger")
api.add_router("/accounts", accounts_router)
api.add_router("/transactions", transactions_router)
api.add_router("/holds", holds_router)


class ErrorOut(Schema):
    code: str
    detail: str


@api.exception_handler(LedgerError)
def ledger_error(request: HttpRequest, exc: LedgerError) -> HttpResponse:
    return api.create_response(
        request, ErrorOut(code=exc.code, detail=exc.detail), status=int(exc.status)
    )


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
