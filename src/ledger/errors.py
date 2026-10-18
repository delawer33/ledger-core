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


class InvalidEntriesError(LedgerError):
    code = "invalid_entries"


class EntriesNotZeroSumError(LedgerError):
    code = "entries_not_zero_sum"


class CurrencyMismatchError(LedgerError):
    code = "currency_mismatch"


class InsufficientFundsError(LedgerError):
    code = "insufficient_funds"


class IdempotencyKeyInUseError(LedgerError):
    """Placeholder until the idempotency protocol replays the stored response."""

    code = "idempotency_key_in_use"
    status = HTTPStatus.CONFLICT


class MissingHeaderError(LedgerError):
    code = "missing_header"
    status = HTTPStatus.BAD_REQUEST
