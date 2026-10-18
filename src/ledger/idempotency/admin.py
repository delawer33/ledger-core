from django.contrib import admin

from ledger.idempotency.models import IdempotencyRecord
from ledger.readonly_admin import ReadOnlyModelAdmin


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(ReadOnlyModelAdmin[IdempotencyRecord]):
    list_display = ("id", "source_system", "key", "created_at")
    list_filter = ("source_system",)
    search_fields = ("key", "source_system")
