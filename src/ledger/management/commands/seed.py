from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from ledger.accounts.system import seed_system_accounts


class Command(BaseCommand):
    help = (
        "Create the System Accounts (fee, bank in transit, clearing) and, when "
        "DJANGO_SUPERUSER_USERNAME/PASSWORD are set, the admin superuser. Safe to run repeatedly."
    )

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
        self.stdout.write(_seed_superuser())


def _seed_superuser() -> str:
    username = settings.DJANGO_SUPERUSER_USERNAME
    password = settings.DJANGO_SUPERUSER_PASSWORD
    if not username or not password:
        return "Superuser: skipped (DJANGO_SUPERUSER_USERNAME/PASSWORD not set)"
    if settings.ENVIRONMENT == "production":
        return "Superuser: skipped (ENVIRONMENT=production)"
    user_model = get_user_model()
    if user_model.objects.filter(username=username).exists():
        return f"Superuser {username}: already exists"
    user_model.objects.create_superuser(
        username=username, email=settings.DJANGO_SUPERUSER_EMAIL, password=password
    )
    return f"Superuser {username}: created"
