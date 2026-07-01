# tests/test_edim_fx.py
"""
Regression: edim.commit_batch built its JE lines from the mapped payload but
dropped the payload's currency/exchange_rate (which validators.py validates). So
a FOREIGN-currency CSV import was stamped in the functional currency and booked
at a silent 1:1 (e.g. USD 20 as EGP 20) — it bypassed the post_journal_entry FX
guard because the transaction currency was gone.

commit_batch now threads currency + exchange_rate onto the lines and the entry
header, so a foreign import converts with a rate on file or quarantines (fails
the commit) when none exists.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounts.authz import system_actor_for_company


def _validated_foreign_batch(company):
    """A VALIDATED, AUTO_POST batch: one balanced USD journal entry (2 lines) on
    an EGP-functional company (so the lines are foreign)."""
    from accounting.models import Account
    from edim.models import IngestionBatch, MappingProfile, SourceSystem, StagedRecord
    from projections.write_barrier import projection_writes_allowed

    company.functional_currency = "EGP"
    company.save(update_fields=["functional_currency"])

    with projection_writes_allowed():
        Account.objects.projection().create(
            company=company,
            code="1000",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        Account.objects.projection().create(
            company=company,
            code="4000",
            name="Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )

    src = SourceSystem.objects.create(
        company=company,
        code="ERP1",
        name="Ext ERP",
        system_type=SourceSystem.SystemType.ERP,
        trust_level=SourceSystem.TrustLevel.FINANCIAL,
    )
    profile = MappingProfile.objects.create(
        company=company,
        source_system=src,
        name="JE profile",
        document_type=MappingProfile.DocumentType.JOURNAL,
        status=MappingProfile.ProfileStatus.ACTIVE,
        posting_policy=MappingProfile.PostingPolicy.AUTO_POST,
    )
    batch = IngestionBatch.objects.create(
        company=company,
        source_system=src,
        mapping_profile=profile,
        ingestion_type=IngestionBatch.IngestionType.FILE_CSV,
        status=IngestionBatch.Status.VALIDATED,
        original_filename="je.csv",
    )
    StagedRecord.objects.create(
        batch=batch,
        company=company,
        row_number=1,
        raw_payload={},
        row_hash="h1",
        is_valid=True,
        mapped_payload={"date": "2026-06-15", "account_code": "1000", "debit": "20", "credit": "0", "currency": "USD"},
    )
    StagedRecord.objects.create(
        batch=batch,
        company=company,
        row_number=2,
        raw_payload={},
        row_hash="h2",
        is_valid=True,
        mapped_payload={"date": "2026-06-15", "account_code": "4000", "debit": "0", "credit": "20", "currency": "USD"},
    )
    return batch


@pytest.mark.django_db
def test_edim_foreign_import_without_rate_is_quarantined(company, owner_membership):
    from edim.commands import commit_batch

    batch = _validated_foreign_batch(company)
    actor = system_actor_for_company(company)

    with pytest.raises(Exception) as exc:  # commit_batch raises on post failure
        commit_batch(actor, batch.id)
    assert "exchange rate" in str(exc.value).lower()  # quarantined, not booked 1:1


@pytest.mark.django_db
def test_edim_foreign_import_with_rate_converts(company, owner_membership):
    from accounting.models import ExchangeRate, JournalEntry, JournalLine
    from edim.commands import commit_batch

    batch = _validated_foreign_batch(company)
    ExchangeRate.objects.create(
        company=company,
        from_currency="USD",
        to_currency="EGP",
        rate=Decimal("48"),
        effective_date=date(2026, 6, 1),
        rate_type="SPOT",
    )
    actor = system_actor_for_company(company)

    result = commit_batch(actor, batch.id)
    assert result.success, result.error

    batch.refresh_from_db()
    je = JournalEntry.objects.get(company=company, public_id=batch.committed_entry_public_ids[0])
    lines = {ln.account.code: ln for ln in JournalLine.objects.filter(entry=je).select_related("account")}
    assert lines["1000"].debit == Decimal("960.00")  # 20 USD * 48 -> EGP, NOT 20
    assert lines["4000"].credit == Decimal("960.00")
