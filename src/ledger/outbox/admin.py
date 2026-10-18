from django.contrib import admin

from ledger.outbox.models import DeliveredEvent, OutboxEvent
from ledger.readonly_admin import ReadOnlyModelAdmin


@admin.register(OutboxEvent)
class OutboxEventAdmin(ReadOnlyModelAdmin[OutboxEvent]):
    list_display = ("sequence", "type", "event_id", "created_at", "delivered_at", "is_delivered")
    list_filter = ("type", ("delivered_at", admin.EmptyFieldListFilter))
    search_fields = ("event_id",)

    @admin.display(description="Delivered", boolean=True, ordering="delivered_at")
    def is_delivered(self, obj: OutboxEvent) -> bool:
        return obj.delivered_at is not None


@admin.register(DeliveredEvent)
class DeliveredEventAdmin(ReadOnlyModelAdmin[DeliveredEvent]):
    list_display = ("id", "sequence", "type", "event_id", "received_at")
    list_filter = ("type",)
    search_fields = ("event_id",)
