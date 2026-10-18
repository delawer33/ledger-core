from django.db import models


class HoldStatus(models.TextChoices):
    ACTIVE = "active"
    CAPTURED = "captured"
    RELEASED = "released"
    EXPIRED = "expired"


class Hold(models.Model):
    """Freezes part of an account's Available Balance. Creates no Entry."""

    account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="holds")
    amount = models.BigIntegerField()
    status = models.CharField(max_length=16, choices=HoldStatus.choices, default=HoldStatus.ACTIVE)
    expires_at = models.DateTimeField()
    source_system = models.CharField(max_length=255)
    key = models.CharField(max_length=255)
    captured_transaction = models.ForeignKey(
        "ledger.Transaction",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="captured_holds",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="hold_amount_positive"),
            models.CheckConstraint(
                condition=models.Q(status__in=HoldStatus.values), name="hold_status_valid"
            ),
            models.UniqueConstraint(
                fields=["source_system", "key"], name="hold_idempotency_unique"
            ),
        ]
        indexes = [
            # The expiry task scans active holds by deadline.
            models.Index(
                fields=["expires_at"],
                condition=models.Q(status="active"),
                name="hold_active_expires_idx",
            ),
        ]
