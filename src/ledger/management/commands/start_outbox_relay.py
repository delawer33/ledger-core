from typing import Any

from django.core.management.base import BaseCommand

from ledger.outbox.tasks import relay_outbox


class Command(BaseCommand):
    help = (
        "Enqueue the first outbox relay task; it reschedules itself. Run db_worker to execute it."
    )

    def handle(self, *_args: Any, **_options: Any) -> None:
        relay_outbox.enqueue()
        self.stdout.write("Outbox relay enqueued")
