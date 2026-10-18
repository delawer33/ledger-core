"""Holds: freeze part of Available Balance, then capture, release or let expire."""

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from ledger.accounts.models import Account
from ledger.balances.service import available_amount
from ledger.errors import (
    AccountNotFoundError,
    AmountOutOfRangeError,
    CaptureExceedsHoldError,
    HoldNotActiveError,
    HoldNotFoundError,
    InsufficientFundsError,
    InvalidHoldError,
)
from ledger.holds.models import Hold, HoldStatus
from ledger.limits import MAX_AMOUNT, MAX_TTL_SECONDS
from ledger.outbox.service import emit
from ledger.posting.models import Transaction, TransactionKind
from ledger.posting.service import EntryLine, post_transaction

EXPIRE_BATCH_SIZE = 100


def get_hold(hold_id: int) -> Hold:
    try:
        return Hold.objects.get(pk=hold_id)
    except Hold.DoesNotExist:
        raise HoldNotFoundError(f"hold {hold_id} does not exist") from None


def place_hold(
    *, account_id: int, amount: int, ttl_seconds: int, source_system: str, key: str
) -> Hold:
    if amount <= 0:
        raise InvalidHoldError("hold amount must be positive")
    if ttl_seconds <= 0:
        raise InvalidHoldError("hold ttl must be positive")
    if amount > MAX_AMOUNT:
        raise AmountOutOfRangeError(f"hold amount must not exceed {MAX_AMOUNT}")
    if ttl_seconds > MAX_TTL_SECONDS:
        raise InvalidHoldError(f"hold ttl must not exceed {MAX_TTL_SECONDS} seconds")
    with transaction.atomic():
        # Same lock as the posting engine takes, so Available cannot change under us.
        account = Account.objects.select_for_update().filter(pk=account_id).first()
        if account is None:
            raise AccountNotFoundError(f"account {account_id} does not exist")
        if available_amount(account_id) < amount:
            raise InsufficientFundsError(f"account {account_id} has less than {amount} available")
        hold = Hold.objects.create(
            account=account,
            amount=amount,
            expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
            source_system=source_system,
            key=key,
        )
        _emit("hold.placed", hold)
    return hold


def capture_hold(
    *,
    hold_id: int,
    to_account_id: int,
    amount: int | None,
    source_system: str,
    key: str,
) -> Hold:
    """Capture the hold fully or partially; whatever is not captured becomes available again."""
    with transaction.atomic():
        hold = _lock_active(hold_id)
        captured = hold.amount if amount is None else amount
        if captured <= 0:
            raise InvalidHoldError("capture amount must be positive")
        if captured > hold.amount:
            raise CaptureExceedsHoldError(f"capture {captured} exceeds hold amount {hold.amount}")
        # Close first and save: the posting engine reads active holds from the database and must
        # not count this one against the account. A failed posting rolls the close back (ADR-0011).
        hold.status = HoldStatus.CAPTURED
        hold.save(update_fields=["status"])
        posted = post_transaction(
            kind=TransactionKind.HOLD_CAPTURE,
            lines=[EntryLine(hold.account_id, -captured), EntryLine(to_account_id, captured)],
            source_system=source_system,
            key=key,
        )
        hold.captured_transaction = posted
        hold.save(update_fields=["captured_transaction"])
        _emit("hold.captured", hold, captured_amount=captured, posted=posted)
    return hold


def release_hold(*, hold_id: int) -> Hold:
    with transaction.atomic():
        hold = _lock_active(hold_id)
        _close(hold, HoldStatus.RELEASED, reason="released")
    return hold


def expire_due_holds(*, now: datetime | None = None) -> int:
    """Release active holds past their deadline, in batches that skip rows others hold."""
    cutoff = now or timezone.now()
    expired = 0
    while True:
        with transaction.atomic():
            batch = list(
                Hold.objects.select_for_update(skip_locked=True)
                .filter(status=HoldStatus.ACTIVE, expires_at__lte=cutoff)
                .order_by("expires_at", "pk")[:EXPIRE_BATCH_SIZE]
            )
            for hold in batch:
                _close(hold, HoldStatus.EXPIRED, reason="expired")
        expired += len(batch)
        if len(batch) < EXPIRE_BATCH_SIZE:
            return expired


def _lock_active(hold_id: int) -> Hold:
    hold = Hold.objects.select_for_update().filter(pk=hold_id).first()
    if hold is None:
        raise HoldNotFoundError(f"hold {hold_id} does not exist")
    if hold.status != HoldStatus.ACTIVE:
        raise HoldNotActiveError(f"hold {hold_id} is {hold.status}")
    return hold


def _close(hold: Hold, status: HoldStatus, *, reason: str) -> None:
    hold.status = status
    hold.save(update_fields=["status"])
    _emit("hold.released", hold, reason=reason)


def _emit(
    event_type: str,
    hold: Hold,
    *,
    captured_amount: int | None = None,
    posted: Transaction | None = None,
    reason: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "hold_id": hold.pk,
        "account_id": hold.account_id,
        "amount": hold.amount,
        "status": hold.status,
        "expires_at": hold.expires_at.isoformat(),
    }
    if captured_amount is not None and posted is not None:
        payload["captured_amount"] = captured_amount
        payload["transaction_id"] = posted.pk
    if reason is not None:
        payload["reason"] = reason
    emit(event_type, payload)
