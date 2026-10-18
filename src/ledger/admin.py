"""Django discovers admin classes here; each domain area defines its own in its package."""

from ledger.accounts import admin as accounts_admin
from ledger.balances import admin as balances_admin
from ledger.chains import admin as chains_admin
from ledger.holds import admin as holds_admin
from ledger.idempotency import admin as idempotency_admin
from ledger.outbox import admin as outbox_admin
from ledger.posting import admin as posting_admin
from ledger.reconciliation import admin as reconciliation_admin

__all__ = [
    "accounts_admin",
    "balances_admin",
    "chains_admin",
    "holds_admin",
    "idempotency_admin",
    "outbox_admin",
    "posting_admin",
    "reconciliation_admin",
]
