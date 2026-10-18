from django.contrib import admin

from ledger.readonly_admin import ReadOnlyModelAdmin
from ledger.reconciliation.models import Incident


@admin.register(Incident)
class IncidentAdmin(ReadOnlyModelAdmin[Incident]):
    list_display = ("id", "account", "expected", "actual", "opened_at", "closed_at", "is_open")
    list_filter = (("closed_at", admin.EmptyFieldListFilter),)
    search_fields = ("account__owner",)
    list_select_related = ("account",)

    @admin.display(description="Open", boolean=True)
    def is_open(self, obj: Incident) -> bool:
        return obj.closed_at is None
