# accounting/signals.py
"""Signal guards for the accounting app.

A129b / ADR-0001 prerequisite P6 — bank-statement deletion guard.

Deleting a matched ``accounting.BankStatement`` is dangerous: its lines
cascade-delete, but ``BankStatementLine.matched_journal_line`` is
``on_delete=SET_NULL``, so the posted settlement-clearance JE survives with no
reverser run — an orphaned financial entry, and the EBD line it drained stays
reconciled. (See the Shopify_R 2026-05-28 incident.)

This ``pre_delete`` signal blocks deletion of a BankStatement that still has
matched lines, UNLESS the caller has entered ``statement_delete_allowed()``.
Legitimate deleters enter that context:

- ``reconciliation.commands.unmatch_and_delete_statement`` — after it reverses
  every match (which reverses the clearance JEs and releases the EBD residual).
- company offboarding (``accounts.commands.delete_unverified_user`` →
  ``company.delete()`` cascade). NOTE: for a *bootstrapped* company this guard
  is never reached — ``company.delete()`` is preempted by ``ProtectedError``
  during the collector's collect() phase (``BankStatement.account`` and several
  other FKs are ``on_delete=PROTECT``). The wrap therefore only matters for an
  unverified company with no chart of accounts. Any real tenant-teardown must
  route statements through ``unmatch_and_delete_statement`` (or a raw-SQL clear
  like ``backups.importer``) *before* deleting accounts.
- demo reseed (``seed_shopify_demo._flush``).

Restore-clear (``backups.importer._clear_company_data``) uses raw SQL, so it
bypasses this signal by design — do not convert it to ORM deletes without
routing through ``statement_delete_allowed()``.

Why a ``pre_delete`` signal and not an overridden ``Model.delete()``: an
override fires on neither cascade collection, ``QuerySet.delete()``, nor raw
SQL — i.e. on none of the real delete paths in this codebase — while a
``pre_delete`` signal fires per-object on both instance and cascade/queryset
deletes.
"""

from __future__ import annotations

from projections.write_barrier import is_statement_delete_allowed


class StatementDeletionBlocked(Exception):
    """Raised when a matched BankStatement is deleted outside the sanctioned
    unmatch-then-delete flow. Catch + route through
    ``reconciliation.commands.unmatch_and_delete_statement`` instead."""


def guard_bank_statement_delete(sender, instance, **kwargs):
    """pre_delete receiver for accounting.BankStatement (connected in
    AccountingConfig.ready())."""
    if is_statement_delete_allowed():
        return

    # Local import to avoid app-loading cycles at import time.
    from accounting.models import BankStatementLine

    matched_statuses = [
        BankStatementLine.MatchStatus.AUTO_MATCHED,
        BankStatementLine.MatchStatus.MANUAL_MATCHED,
        BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE,
    ]
    if BankStatementLine.objects.filter(statement=instance, match_status__in=matched_statuses).exists():
        raise StatementDeletionBlocked(
            f"BankStatement {instance.pk} has matched lines; deleting it would orphan the "
            f"posted clearance JE(s) and/or leave stranded reconciled journal lines. Use "
            f"reconciliation.commands.unmatch_and_delete_statement (which reverses the matches "
            f"first), or enter statement_delete_allowed() explicitly."
        )
