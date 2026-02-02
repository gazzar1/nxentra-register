# tests/test_write_barrier.py
"""
Tests for write barrier enforcement.
"""

import pytest
from uuid import uuid4

from rest_framework import serializers

from accounts.models import Company
from accounting.models import CompanySequence
from projections.write_barrier import command_writes_allowed, bootstrap_writes_allowed


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ("name", "slug")


@pytest.mark.django_db
def test_direct_model_save_raises(settings):
    settings.TESTING = False

    with bootstrap_writes_allowed():
        company = Company.objects.create(
            name="Barrier Co",
            slug=f"barrier-{uuid4()}",
        )

    company.name = "Barrier Co Updated"
    with pytest.raises(RuntimeError, match="Direct saves are only allowed"):
        company.save()


@pytest.mark.django_db
def test_direct_model_create_in_serializer_raises(settings):
    settings.TESTING = False

    serializer = CompanySerializer(
        data={"name": "Serializer Co", "slug": f"serializer-{uuid4()}"},
    )
    serializer.is_valid(raise_exception=True)

    with pytest.raises(RuntimeError, match="Direct saves are only allowed"):
        serializer.save()


@pytest.mark.django_db
def test_command_context_allows_writes(settings):
    settings.TESTING = False

    with bootstrap_writes_allowed():
        company = Company.objects.create(
            name="Sequence Co",
            slug=f"sequence-{uuid4()}",
        )

    with pytest.raises(RuntimeError, match="command_writes_allowed"):
        CompanySequence.objects.create(company=company, name="journal_entry_number")

    with command_writes_allowed():
        seq = CompanySequence.objects.create(company=company, name="journal_entry_number")

    assert seq.company_id == company.id
