from django.db import models


class Incident(models.Model):
    """A mismatch between a snapshot and the recount; closed only by hand."""

    account = models.ForeignKey(
        "ledger.Account", on_delete=models.PROTECT, related_name="incidents"
    )
    expected = models.BigIntegerField()
    actual = models.BigIntegerField()
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            # One open Incident per account: a repeated run must not pile up duplicates.
            models.UniqueConstraint(
                fields=["account"],
                condition=models.Q(closed_at__isnull=True),
                name="incident_one_open_per_account",
            ),
        ]


class IncidentRepair(models.Model):
    """One manual snapshot repair: was, became, who, why, when. Append-only (ADR-0017).

    Immutable like Entry: a database trigger rejects UPDATE and DELETE.
    """

    incident = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="repairs")
    repaired_from = models.BigIntegerField()
    repaired_to = models.BigIntegerField()
    note = models.TextField()
    actor = models.CharField(max_length=255)
    repaired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
