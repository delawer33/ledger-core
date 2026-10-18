from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ledger.errors import LedgerError
from ledger.reconciliation.service import close_incident


class Command(BaseCommand):
    help = "Close an open Incident after investigation; its snapshot becomes trusted again."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("incident_id", type=int)
        parser.add_argument("--note", required=True, help="what was found and done")

    def handle(self, *_args: Any, **options: Any) -> None:
        try:
            incident = close_incident(options["incident_id"], note=options["note"])
        except LedgerError as error:
            raise CommandError(f"{error.code}: {error.detail}") from error
        self.stdout.write(f"Incident {incident.pk} closed; account {incident.account_id} trusted")
