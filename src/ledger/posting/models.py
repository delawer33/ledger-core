from django.db import models


class TransactionKind(models.TextChoices):
    TRANSFER = "transfer"
    FEE = "fee"
    PAYOUT = "payout"
    HOLD_CAPTURE = "hold_capture"
    REVERSAL = "reversal"


class Transaction(models.Model):
    """Atomic business operation. Has no status: reversed means another row points at it."""

    kind = models.CharField(max_length=16, choices=TransactionKind.choices)
    source_system = models.CharField(max_length=255)
    key = models.CharField(max_length=255)
    stored_response = models.JSONField(null=True)
    reversal_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversals"
    )
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "key"], name="transaction_idempotency_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=TransactionKind.values), name="transaction_kind_valid"
            ),
            # A transaction can be reversed only once.
            models.UniqueConstraint(
                fields=["reversal_of"],
                condition=models.Q(reversal_of__isnull=False),
                name="transaction_reversed_once",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="reversal", reversal_of__isnull=False)
                | (~models.Q(kind="reversal") & models.Q(reversal_of__isnull=True)),
                name="transaction_reversal_of_iff_reversal",
            ),
        ]


class Entry(models.Model):
    """Immutable ledger line; UPDATE and DELETE are rejected by a database trigger."""

    transaction = models.ForeignKey(
        "ledger.Transaction", on_delete=models.PROTECT, related_name="entries"
    )
    account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="entries")
    amount = models.BigIntegerField()
