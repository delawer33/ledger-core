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
    # Filled by repair_snapshot: what the snapshot was, what it became, when and why (ADR-0017).
    repaired_from = models.BigIntegerField(null=True, blank=True)
    repaired_to = models.BigIntegerField(null=True, blank=True)
    repaired_at = models.DateTimeField(null=True, blank=True)
    repair_note = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            # One open Incident per account: a repeated run must not pile up duplicates.
            models.UniqueConstraint(
                fields=["account"],
                condition=models.Q(closed_at__isnull=True),
                name="incident_one_open_per_account",
            ),
        ]
