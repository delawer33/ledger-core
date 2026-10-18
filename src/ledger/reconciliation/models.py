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
