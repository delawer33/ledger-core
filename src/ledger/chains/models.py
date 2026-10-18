import uuid

from django.db import models


class TaskChain(models.Model):
    """The single live chain of a self-rescheduling task: its current token and lease."""

    name = models.CharField(max_length=64, primary_key=True)
    # Tasks of the chain carry the token; a task holding any other token is superseded and exits.
    token = models.UUIDField(default=uuid.uuid4)
    # The chain is presumed dead when this passes without being renewed by a pass.
    lease_until = models.DateTimeField()
