from django.contrib import admin

from ledger.readonly_admin import ReadOnlyInline, ReadOnlyModelAdmin
from ledger.reconciliation.models import Incident, IncidentRepair


class IncidentRepairInline(ReadOnlyInline):
    model = IncidentRepair
    fields = ("repaired_at", "actor", "repaired_from", "repaired_to", "note")
    readonly_fields = fields


@admin.register(Incident)
class IncidentAdmin(ReadOnlyModelAdmin[Incident]):
    list_display = ("id", "account", "expected", "actual", "opened_at", "closed_at", "is_open")
    inlines = (IncidentRepairInline,)
    list_filter = (("closed_at", admin.EmptyFieldListFilter),)
    search_fields = ("account__owner",)
    list_select_related = ("account",)

    @admin.display(description="Open", boolean=True)
    def is_open(self, obj: Incident) -> bool:
        return obj.closed_at is None
