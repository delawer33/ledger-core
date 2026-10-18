from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from ledger.accounts.system import seed_system_accounts


class Command(BaseCommand):
    help = "Create the System Accounts (fee, bank in transit, clearing). Safe to run repeatedly."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--currency",
            action="append",
            dest="currencies",
            help="ISO 4217 code; repeat for several currencies (default: RUB)",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        currencies: list[str] = options["currencies"] or ["RUB"]
        with transaction.atomic():
            created = seed_system_accounts(currencies)
        self.stdout.write(f"System accounts: {created} created")
