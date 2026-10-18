from typing import Any

from django.core.management.base import BaseCommand

from ledger.outbox.tasks import ensure_outbox_relay


class Command(BaseCommand):
    help = (
        "Start the outbox relay chain unless a live one exists (safe to repeat, also from cron: "
        "it revives a chain whose worker died). Run db_worker to execute it."
    )

    def handle(self, *_args: Any, **_options: Any) -> None:
        if ensure_outbox_relay():
            self.stdout.write("Outbox relay started")
        else:
            self.stdout.write("Outbox relay already running")
