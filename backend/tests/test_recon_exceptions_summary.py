# tests/test_recon_exceptions_summary.py
"""The recon summary endpoint surfaces the (previously orphaned) exception
queue under `exceptions`, so the detect → investigate → resolve lifecycle is
reachable from /finance/reconciliation. Read-only rollup that mirrors
bank_connector's ExceptionSummaryView (open states only)."""

from datetime import date

from accounting.reconciliation_views import _exceptions_summary
from bank_connector.models import ReconciliationException

Sev = ReconciliationException.Severity
Status = ReconciliationException.Status
Type = ReconciliationException.ExceptionType


def _exc(company, *, severity, status=Status.OPEN, exception_type=Type.UNMATCHED_BANK_TX):
    return ReconciliationException.objects.create(
        company=company,
        exception_type=exception_type,
        severity=severity,
        status=status,
        title="test exception",
        exception_date=date.today(),
    )


def test_exceptions_summary_counts_only_open_states(db, company):
    _exc(company, severity=Sev.CRITICAL, exception_type=Type.UNMATCHED_BANK_TX)
    _exc(company, severity=Sev.HIGH, status=Status.IN_PROGRESS, exception_type=Type.UNMATCHED_PAYOUT)
    _exc(company, severity=Sev.HIGH, status=Status.ESCALATED, exception_type=Type.UNMATCHED_PAYOUT)
    # Resolved + dismissed must NOT count toward the open queue.
    _exc(company, severity=Sev.CRITICAL, status=Status.RESOLVED)
    _exc(company, severity=Sev.LOW, status=Status.DISMISSED)

    summary = _exceptions_summary(company)

    assert summary["available"] is True
    assert summary["total_open"] == 3  # OPEN + IN_PROGRESS + ESCALATED only
    assert summary["by_severity"][Sev.CRITICAL] == 1
    assert summary["by_severity"][Sev.HIGH] == 2
    assert summary["by_severity"][Sev.LOW] == 0  # the only LOW row is dismissed
    # by_type omits zero-count types and reflects only open rows.
    assert summary["by_type"][Type.UNMATCHED_BANK_TX] == 1
    assert summary["by_type"][Type.UNMATCHED_PAYOUT] == 2
    assert Type.MISSING_JE not in summary["by_type"]


def test_exceptions_summary_empty_company_is_available_and_zeroed(db, company):
    summary = _exceptions_summary(company)
    assert summary["available"] is True
    assert summary["total_open"] == 0
    assert summary["by_type"] == {}
    assert all(v == 0 for v in summary["by_severity"].values())
