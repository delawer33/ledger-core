"""Domain errors: every expected failure carries the machine code of the violated rule."""

from http import HTTPStatus


class LedgerError(Exception):
    """A violated rule. `code` is stable and meant for clients; `detail` is for humans."""

    code: str = "ledger_error"
    status: int = HTTPStatus.UNPROCESSABLE_ENTITY

    def __init__(self, detail: str, *, code: str | None = None, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status


class AccountNotFoundError(LedgerError):
    code = "account_not_found"
    status = HTTPStatus.NOT_FOUND


class TransactionNotFoundError(LedgerError):
    code = "transaction_not_found"
    status = HTTPStatus.NOT_FOUND


class TransactionAlreadyReversedError(LedgerError):
    code = "transaction_already_reversed"


class InvalidEntriesError(LedgerError):
    code = "invalid_entries"


class EntriesNotZeroSumError(LedgerError):
    code = "entries_not_zero_sum"


class CurrencyMismatchError(LedgerError):
    code = "currency_mismatch"


class InsufficientFundsError(LedgerError):
    code = "insufficient_funds"


class IdempotencyKeyReusedError(LedgerError):
    """The Idempotency Key was already used for a different request."""

    code = "idempotency_key_reused"
    status = HTTPStatus.CONFLICT


class MissingHeaderError(LedgerError):
    code = "missing_header"
    status = HTTPStatus.BAD_REQUEST


class HeaderTooLongError(LedgerError):
    code = "header_too_long"
    status = HTTPStatus.BAD_REQUEST


class AmountOutOfRangeError(LedgerError):
    code = "amount_out_of_range"


class BalanceOverflowError(LedgerError):
    code = "balance_overflow"


class IncidentNotFoundError(LedgerError):
    code = "incident_not_found"
    status = HTTPStatus.NOT_FOUND


class IncidentNoteRequiredError(LedgerError):
    code = "incident_note_required"


class SnapshotStillDivergesError(LedgerError):
    """The snapshot differs from the recount; only an explicit repair_snapshot fixes that."""

    code = "snapshot_still_diverges"


class HoldNotFoundError(LedgerError):
    code = "hold_not_found"
    status = HTTPStatus.NOT_FOUND


class HoldNotActiveError(LedgerError):
    code = "hold_not_active"


class CaptureExceedsHoldError(LedgerError):
    code = "capture_exceeds_hold"


class InvalidHoldError(LedgerError):
    code = "invalid_hold"


class WorkerPresumedDead(LedgerError):
    """Recorded as the exception class of a task pass whose worker is presumed dead.

    `chains.service.fail_orphaned_runs` marks the RUNNING row of a reassigned chain FAILED with
    this class's dotted path, so `TaskError.exception_class` of such a row imports (ADR-0012).
    It is never raised: the worker that would have raised it is gone.
    """

    code = "worker_presumed_dead"
