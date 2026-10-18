from django.contrib import admin

from ledger.balances.models import BalanceSnapshot
from ledger.readonly_admin import ReadOnlyModelAdmin


@admin.register(BalanceSnapshot)
class BalanceSnapshotAdmin(ReadOnlyModelAdmin[BalanceSnapshot]):
    list_display = ("account", "posted_amount", "version", "trusted")
    list_filter = ("trusted",)
    search_fields = ("account__owner",)
    list_select_related = ("account",)
