"""Ledger-wide invariants as one reusable check, for concurrency proofs and the load generator."""

from dataclasses import dataclass, field

from ledger.accounts.models import Account
from ledger.balances.models import BalanceSnapshot
from ledger.balances.service import active_holds_total, recount_posted
from ledger.posting.service import NON_NEGATIVE_TYPES


class InvariantViolationError(AssertionError):
    """The ledger broke a guarantee; carries every violation found, not just the first."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


@dataclass(frozen=True)
class InvariantReport:
    accounts_checked: int
    posted_total: int
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def check_invariants() -> InvariantReport:
    """Check the guarantees over all accounts; call it when no writer is mid-flight.

    - the Posted Balances of all accounts sum to zero (Entries of every Transaction sum to zero);
    - no Available Balance of an asset or liability account is negative;
    - every Balance Snapshot equals the recount of the account's Entries.
    """
    violations: list[str] = []
    accounts = list(Account.objects.order_by("pk"))
    snapshots = dict(BalanceSnapshot.objects.values_list("account_id", "posted_amount"))
    posted_total = 0
    for account in accounts:
        snapshot = snapshots.get(account.pk)
        recounted = recount_posted(account.pk)
        posted_total += recounted
        if snapshot is None:
            violations.append(f"account {account.pk} has no snapshot")
        elif snapshot != recounted:
            violations.append(
                f"account {account.pk} snapshot {snapshot} differs from recount {recounted}"
            )
        available = recounted - active_holds_total(account.pk)
        if account.type in NON_NEGATIVE_TYPES and available < 0:
            violations.append(f"account {account.pk} available balance is {available}")
    if posted_total != 0:
        violations.append(f"posted balances sum to {posted_total}, expected 0")
    return InvariantReport(len(accounts), posted_total, violations)


def assert_invariants() -> InvariantReport:
    report = check_invariants()
    if not report.ok:
        raise InvariantViolationError(report.violations)
    return report
