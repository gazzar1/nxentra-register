# A79 Phase 1: bind a default PostingProfile to each Customer and Vendor.
# The invoice/bill form will auto-fill the posting profile when the user
# picks the customer/vendor, eliminating the "pick the same dropdown 100x"
# friction. Profile is the richer routing primitive (control account +
# future tax/terms/dimension defaults); the bare default_ar_account /
# default_ap_account fields stay for backwards compat.
#
# Backfill strategy (per customer/vendor):
#   1. If the record's default_ar_account / default_ap_account is set,
#      find a MANUAL profile of the matching type whose control_account
#      equals that account. Pick the is_default=True one if there are
#      multiple matches.
#   2. Otherwise fall back to the company's is_default=True MANUAL profile
#      of that type.
#   3. If neither exists, leave the field NULL (the invoice form will
#      surface the dropdown again for that customer/vendor).

from django.db import migrations, models
import django.db.models.deletion


def _backfill_customer_profiles(apps, schema_editor):
    Customer = apps.get_model("accounting", "Customer")
    PostingProfile = apps.get_model("sales", "PostingProfile")

    for customer in Customer.objects.filter(default_posting_profile__isnull=True):
        profile = _resolve_profile(
            PostingProfile,
            company=customer.company,
            profile_type="CUSTOMER",
            account_id=customer.default_ar_account_id,
        )
        if profile is not None:
            customer.default_posting_profile = profile
            customer.save(update_fields=["default_posting_profile"])


def _backfill_vendor_profiles(apps, schema_editor):
    Vendor = apps.get_model("accounting", "Vendor")
    PostingProfile = apps.get_model("sales", "PostingProfile")

    for vendor in Vendor.objects.filter(default_posting_profile__isnull=True):
        profile = _resolve_profile(
            PostingProfile,
            company=vendor.company,
            profile_type="VENDOR",
            account_id=vendor.default_ap_account_id,
        )
        if profile is not None:
            vendor.default_posting_profile = profile
            vendor.save(update_fields=["default_posting_profile"])


def _resolve_profile(PostingProfile, *, company, profile_type, account_id):
    base = PostingProfile.objects.filter(
        company=company,
        profile_type=profile_type,
        usage="MANUAL",
        is_active=True,
    )

    if account_id is not None:
        # Prefer a profile whose control_account matches the record's
        # existing default_ar/ap_account, with is_default winning ties.
        match = base.filter(control_account_id=account_id).order_by("-is_default", "id").first()
        if match is not None:
            return match

    return base.filter(is_default=True).order_by("id").first() or base.order_by("id").first()


def _backfill(apps, schema_editor):
    _backfill_customer_profiles(apps, schema_editor)
    _backfill_vendor_profiles(apps, schema_editor)


def _noop_reverse(apps, schema_editor):
    # Reverse just drops the FK column; no need to undo data.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0032_strip_company_id_from_je_entry_numbers"),
        ("sales", "0012_postingprofile_usage"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="default_posting_profile",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Default posting profile auto-filled on new invoices for "
                    "this customer. Should be a CUSTOMER + MANUAL profile."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="default_for_customers",
                to="sales.postingprofile",
            ),
        ),
        migrations.AddField(
            model_name="vendor",
            name="default_posting_profile",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Default posting profile auto-filled on new bills/POs for "
                    "this vendor. Should be a VENDOR + MANUAL profile."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="default_for_vendors",
                to="sales.postingprofile",
            ),
        ),
        migrations.RunPython(_backfill, _noop_reverse),
    ]
