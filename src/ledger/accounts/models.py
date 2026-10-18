from django.db import models


class AccountType(models.TextChoices):
    ASSET = "asset"
    LIABILITY = "liability"
    REVENUE = "revenue"
    EXPENSE = "expense"
    EQUITY = "equity"


class Account(models.Model):
    """A single-currency account. Holds no balance: see BalanceSnapshot."""

    currency = models.CharField(max_length=3)
    type = models.CharField(max_length=16, choices=AccountType.choices)
    owner = models.CharField(max_length=255)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(type__in=AccountType.values), name="account_type_valid"
            ),
            # A System Account is identified by its owner label and currency; this is what
            # makes the seed command idempotent even under concurrent runs.
            models.UniqueConstraint(
                fields=["owner", "currency"],
                condition=models.Q(is_system=True),
                name="account_system_owner_currency_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.owner} {self.currency} ({self.type})"
