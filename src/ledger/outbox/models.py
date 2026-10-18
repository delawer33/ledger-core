import uuid

from django.db import models


class OutboxEvent(models.Model):
    """A fact written in the same DB transaction as the change and delivered later."""

    # The primary key is the monotonic delivery sequence; event_id is what consumers dedupe on.
    sequence = models.BigAutoField(primary_key=True)
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    type = models.CharField(max_length=64)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            # The relay scans undelivered events in sequence order.
            models.Index(
                fields=["sequence"],
                condition=models.Q(delivered_at__isnull=True),
                name="outbox_undelivered_idx",
            ),
        ]


class DeliveredEvent(models.Model):
    """The demo consumer's record; the unique event_id drops duplicates."""

    event_id = models.UUIDField(unique=True)
    sequence = models.BigIntegerField()
    type = models.CharField(max_length=64)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
