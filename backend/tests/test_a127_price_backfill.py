# tests/test_a127_price_backfill.py
"""
A127 — `_update_item_defaults` must backfill a blank `default_unit_price` from
the Shopify variant price, but never clobber a price the merchant set by hand.

Context: an Item can end up with a 0 price (e.g. order-line auto-create where the
line item carried no price). A later product sync sees the variant price and
should heal that blank — while leaving any manually-entered price untouched.
The two `Item.objects.create` creation paths already set the price; only the
existing-item backfill path was missing it.
"""

from decimal import Decimal

import pytest

from projections.write_barrier import command_writes_allowed
from sales.models import Item
from shopify_connector.commands import _update_item_defaults


@pytest.fixture
def blank_price_item(company):
    with command_writes_allowed():
        return Item.objects.create(
            company=company,
            code="SNOW-001",
            name="Snowboard Complete",
            item_type="INVENTORY",
            default_unit_price=Decimal("0"),
            costing_method="WEIGHTED_AVERAGE",
            is_active=True,
        )


@pytest.mark.django_db
def test_blank_price_is_backfilled_from_variant(blank_price_item):
    _update_item_defaults(blank_price_item, Decimal("0"), None, None, None, None, price=Decimal("149.00"))
    blank_price_item.refresh_from_db()
    assert blank_price_item.default_unit_price == Decimal("149.00")


@pytest.mark.django_db
def test_manual_price_is_never_clobbered(blank_price_item):
    with command_writes_allowed():
        blank_price_item.default_unit_price = Decimal("99.00")
        blank_price_item.save(update_fields=["default_unit_price"])

    # A later sync carries a different variant price — it must not overwrite the
    # merchant's manual price.
    _update_item_defaults(blank_price_item, Decimal("0"), None, None, None, None, price=Decimal("149.00"))
    blank_price_item.refresh_from_db()
    assert blank_price_item.default_unit_price == Decimal("99.00")


@pytest.mark.django_db
def test_zero_variant_price_leaves_blank_untouched(blank_price_item):
    # If the variant itself has no price, there is nothing to backfill — the
    # blank stays blank rather than being "healed" to a meaningless 0 write.
    _update_item_defaults(blank_price_item, Decimal("0"), None, None, None, None, price=Decimal("0"))
    blank_price_item.refresh_from_db()
    assert blank_price_item.default_unit_price == Decimal("0")


# --- Image backfill (heals items first created via webhook / order line, which
#     don't pull the product image — only the full product sync does) -----------


@pytest.mark.django_db
def test_blank_image_is_backfilled(blank_price_item, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "shopify_connector.commands._download_item_image",
        lambda item, url: calls.append((item.id, url)),
    )
    _update_item_defaults(
        blank_price_item, Decimal("0"), None, None, None, None, image_url="https://cdn.shopify.com/x.jpg"
    )
    assert calls == [(blank_price_item.id, "https://cdn.shopify.com/x.jpg")]


@pytest.mark.django_db
def test_no_image_backfill_when_url_empty(blank_price_item, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "shopify_connector.commands._download_item_image",
        lambda item, url: calls.append(url),
    )
    _update_item_defaults(blank_price_item, Decimal("0"), None, None, None, None, image_url="")
    assert calls == []


@pytest.mark.django_db
def test_existing_image_not_overwritten(blank_price_item, monkeypatch):
    # The item already has a photo (merchant-uploaded or pulled earlier) — a
    # later sync must not re-download or clobber it.
    blank_price_item.image = "items/already-there.jpg"
    calls = []
    monkeypatch.setattr(
        "shopify_connector.commands._download_item_image",
        lambda item, url: calls.append(url),
    )
    _update_item_defaults(
        blank_price_item, Decimal("0"), None, None, None, None, image_url="https://cdn.shopify.com/x.jpg"
    )
    assert calls == []
