from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from ledger.posting.models import Entry, Transaction
from ledger.readonly_admin import ReadOnlyInline, ReadOnlyModelAdmin


class EntryInline(ReadOnlyInline):
    model = Entry
    fields = ("account", "amount")


def _link(tx: Transaction | None) -> SafeString | str:
    if tx is None:
        return "-"
    return format_html(
        '<a href="{}">{}</a>', reverse("admin:ledger_transaction_change", args=[tx.pk]), f"#{tx.pk}"
    )


@admin.register(Transaction)
class TransactionAdmin(ReadOnlyModelAdmin[Transaction]):
    list_display = ("id", "kind", "source_system", "key", "reversal_of_link", "posted_at")
    list_filter = ("kind",)
    search_fields = ("key", "source_system")
    list_select_related = ("reversal_of",)
    inlines = (EntryInline,)
    fields = (
        "kind",
        "source_system",
        "key",
        "posted_at",
        "reversal_of_link",
        "reversed_by_link",
        "stored_response",
    )
    readonly_fields = fields

    def get_queryset(self, request: HttpRequest) -> QuerySet[Transaction]:
        return super().get_queryset(request).select_related("reversal_of")

    @admin.display(description="Reversal of")
    def reversal_of_link(self, obj: Transaction) -> SafeString | str:
        return _link(obj.reversal_of)

    @admin.display(description="Reversed by")
    def reversed_by_link(self, obj: Transaction) -> SafeString | str:
        return _link(obj.reversals.first())


@admin.register(Entry)
class EntryAdmin(ReadOnlyModelAdmin[Entry]):
    list_display = ("id", "transaction", "account", "amount")
    search_fields = ("account__owner", "transaction__key")
    list_select_related = ("transaction", "account")
