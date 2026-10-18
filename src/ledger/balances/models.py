from django.db import models


class BalanceSnapshot(models.Model):
    """Cached Posted Balance of an account; never the source of truth."""

    account = models.OneToOneField(
        "ledger.Account", on_delete=models.PROTECT, related_name="snapshot"
    )
    posted_amount = models.BigIntegerField(default=0)
    version = models.BigIntegerField(default=0)
    trusted = models.BooleanField(default=True)
