from django.contrib import admin
from django.db.models import Exists, OuterRef, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.http import HttpRequest

from ledger.accounts.models import Account
from ledger.holds.models import Hold, HoldStatus
from ledger.posting.models import Entry
from ledger.readonly_admin import ReadOnlyModelAdmin
from ledger.reconciliation.models import Incident


def _sum_subquery(model: type[Entry] | type[Hold], field: str, **filters: object) -> object:
    return Coalesce(
        model.objects.filter(account=OuterRef("pk"), **filters)
        .order_by()
        .values("account")
        .annotate(total=Sum(field))
        .values("total"),
        0,
    )


@admin.register(Account)
class AccountAdmin(ReadOnlyModelAdmin[Account]):
    list_display = (
        "id",
        "owner",
        "currency",
        "type",
        "is_system",
        "posted",
        "available",
        "has_open_incident",
    )
    list_filter = ("type", "currency", "is_system")
    search_fields = ("owner",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Account]:
        # Always a recount from Entries, never the snapshot: the admin shows the truth.
        return (
            super()
            .get_queryset(request)
            .annotate(
                _posted=_sum_subquery(Entry, "amount"),
                _held=_sum_subquery(Hold, "amount", status=HoldStatus.ACTIVE),
                _open_incident=Exists(
                    Incident.objects.filter(account=OuterRef("pk"), closed_at__isnull=True)
                ),
            )
        )

    @admin.display(description="Posted", ordering="_posted")
    def posted(self, obj: Account) -> int:
        return int(obj._posted)  # type: ignore[attr-defined]

    @admin.display(description="Available")
    def available(self, obj: Account) -> int:
        return self.posted(obj) - int(obj._held)  # type: ignore[attr-defined]

    @admin.display(description="Open incident", boolean=True, ordering="_open_incident")
    def has_open_incident(self, obj: Account) -> bool:
        return bool(obj._open_incident)  # type: ignore[attr-defined]
