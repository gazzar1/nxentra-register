# tests/test_backup_filefield_serialization.py
"""
A161 drill finding (2026-07-13): export_company crashed with
`TypeError: Object of type ImageFieldFile is not JSON serializable`
for ANY company whose synced products carry images — sales.Item.image
is a registered backup model field, and neither _serialize_instance's
type conversions nor BackupEncoder handled Django FieldFile. Company
backups were structurally broken for real merchants; the timed restore
drill caught it on its first export.

Fix: File/Image fields serialize as their storage path (the DB value);
media binaries are deliberately not bundled — a restore re-points the
path.
"""

import io
import json
import zipfile

import pytest

from backups.exporter import export_company
from backups.importer import restore_company
from projections.write_barrier import command_writes_allowed
from sales.models import Item

pytestmark = pytest.mark.django_db


@pytest.fixture
def company_with_item_image(company):
    with command_writes_allowed():
        item = Item.objects.create(
            company=company,
            code="IMG-01",
            name="Item with product image",
            item_type=Item.ItemType.SERVICE,
            is_active=True,
        )
        # Assigning a name string sets the FieldFile without touching
        # storage — exactly the state Shopify product sync leaves rows in.
        item.image = "items/drill-test.png"
        item.save(update_fields=["image"])
    return company, item


def test_export_serializes_image_field_as_path(company_with_item_image):
    """RED before the fix: json.dumps raised TypeError on ImageFieldFile."""
    company, _item = company_with_item_image

    zip_bytes, _metadata = export_company(company)

    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    records = json.loads(archive.read("models/sales.Item.json"))
    record = next(r for r in records if r["code"] == "IMG-01")
    assert record["image"] == "items/drill-test.png"


def test_roundtrip_restores_image_path(company_with_item_image):
    company, _item = company_with_item_image
    zip_bytes, _ = export_company(company)

    result = restore_company(company, io.BytesIO(zip_bytes))
    assert result is not None

    restored = Item.objects.get(company=company, code="IMG-01")
    assert restored.image.name == "items/drill-test.png"


def test_empty_image_field_stays_null(company):
    with command_writes_allowed():
        Item.objects.create(
            company=company,
            code="NOIMG-01",
            name="Item without image",
            item_type=Item.ItemType.SERVICE,
            is_active=True,
        )

    zip_bytes, _ = export_company(company)
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    records = json.loads(archive.read("models/sales.Item.json"))
    record = next(r for r in records if r["code"] == "NOIMG-01")
    assert record["image"] in (None, "")
