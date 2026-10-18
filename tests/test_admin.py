import datetime as dt

import pytest
from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ledger.accounts.admin import AccountAdmin
from ledger.accounts.models import Account
from ledger.holds.models import Hold
from ledger.models import Entry, Incident, Transaction


@pytest.fixture
def admin_client(client: Client) -> Client:
    user = User.objects.create_superuser("root", password="pw")
    client.force_login(user)
    return client


@pytest.fixture
def seller() -> Account:
    return Account.objects.create(currency="RUB", type="liability", owner="seller:1")


@pytest.fixture
def bank() -> Account:
    return Account.objects.create(currency="RUB", type="asset", owner="bank")


@pytest.fixture
def transfer(seller: Account, bank: Account) -> Transaction:
    tx = Transaction.objects.create(kind="transfer", source_system="shop", key="k1")
    Entry.objects.create(transaction=tx, account=seller, amount=700)
    Entry.objects.create(transaction=tx, account=bank, amount=-700)
    return tx


@pytest.mark.django_db
def test_every_core_model_is_registered_and_read_only(admin_client: Client) -> None:
    models = [m for m in site._registry if m._meta.app_label == "ledger"]
    assert len(models) == 9
    for model in models:
        label = f"admin:ledger_{model._meta.model_name}_"
        assert admin_client.get(reverse(label + "changelist")).status_code == 200
        assert admin_client.get(reverse(label + "add")).status_code == 403
        assert admin_client.post(reverse(label + "add"), {}).status_code == 403


@pytest.mark.django_db
def test_change_view_post_and_delete_are_forbidden(
    admin_client: Client, seller: Account, transfer: Transaction
) -> None:
    url = reverse("admin:ledger_account_change", args=[seller.pk])

    assert admin_client.get(url).status_code == 200
    assert admin_client.post(url, {"owner": "hacked"}).status_code == 403
    assert (
        admin_client.post(reverse("admin:ledger_account_delete", args=[seller.pk])).status_code
        == 403
    )
    seller.refresh_from_db()
    assert seller.owner == "seller:1"


@pytest.mark.django_db
def test_account_list_shows_posted_available_and_open_incident(
    admin_client: Client, seller: Account, transfer: Transaction
) -> None:
    Hold.objects.create(
        account=seller,
        amount=200,
        expires_at=timezone.now() + dt.timedelta(hours=1),
        source_system="shop",
        key="h1",
    )
    Hold.objects.create(
        account=seller,
        amount=50,
        status="released",
        expires_at=timezone.now() + dt.timedelta(hours=1),
        source_system="shop",
        key="h2",
    )
    Incident.objects.create(account=seller, expected=700, actual=1)

    admin = site._registry[Account]
    assert isinstance(admin, AccountAdmin)
    row = admin.get_queryset(
        admin_client.get(reverse("admin:ledger_account_changelist")).wsgi_request
    ).get(pk=seller.pk)

    assert admin.posted(row) == 700
    assert admin.available(row) == 500
    assert admin.has_open_incident(row) is True


@pytest.mark.django_db
def test_transaction_page_shows_entries_and_reversal_links(
    admin_client: Client, seller: Account, bank: Account, transfer: Transaction
) -> None:
    reversal = Transaction.objects.create(
        kind="reversal", source_system="shop", key="k2", reversal_of=transfer
    )
    Entry.objects.create(transaction=reversal, account=seller, amount=-700)
    Entry.objects.create(transaction=reversal, account=bank, amount=700)

    original = admin_client.get(reverse("admin:ledger_transaction_change", args=[transfer.pk]))
    mirror = admin_client.get(reverse("admin:ledger_transaction_change", args=[reversal.pk]))

    assert (
        reverse("admin:ledger_transaction_change", args=[reversal.pk]) in original.content.decode()
    )
    assert reverse("admin:ledger_transaction_change", args=[transfer.pk]) in mirror.content.decode()
    assert "seller:1" in original.content.decode()


@pytest.mark.django_db
def test_seed_creates_superuser_from_env_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_SUPERUSER_USERNAME", "ops")
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "s3cret-dev")
    monkeypatch.setenv("DJANGO_SUPERUSER_EMAIL", "ops@example.com")

    call_command("seed")
    call_command("seed")

    user = User.objects.get(username="ops")
    assert user.is_superuser
    assert user.is_staff
    assert user.check_password("s3cret-dev")
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_seed_without_superuser_env_creates_no_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DJANGO_SUPERUSER_USERNAME", raising=False)
    monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD", raising=False)

    call_command("seed")

    assert not User.objects.exists()
