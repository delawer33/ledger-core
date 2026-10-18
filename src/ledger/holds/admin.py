from django.contrib import admin

from ledger.holds.models import Hold
from ledger.readonly_admin import ReadOnlyModelAdmin


@admin.register(Hold)
class HoldAdmin(ReadOnlyModelAdmin[Hold]):
    list_display = ("id", "account", "amount", "status", "expires_at", "source_system", "key")
    list_filter = ("status",)
    search_fields = ("account__owner", "key")
    list_select_related = ("account",)
