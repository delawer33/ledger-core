from django.db import models


class IdempotencyRecord(models.Model):
    """Claim on an Idempotency Key, inserted before the operation runs."""

    source_system = models.CharField(max_length=255)
    key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    response = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "key"], name="idempotency_record_unique"
            ),
        ]
