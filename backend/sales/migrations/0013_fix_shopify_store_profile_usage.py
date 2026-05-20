# A79 follow-up: the A78 (sales/0012) migration only flipped profiles
# referenced by SettlementProvider to GATEWAY. The store-level default
# profile referenced by ShopifyStore.default_posting_profile (e.g.
# SHOPIFY-NXENTRA-RE) was missed — it stayed MANUAL.
#
# Three consequences observed on the Shopify_R demo company:
#   1. SHOPIFY-NXENTRA-RE still appeared in manual invoice dropdowns.
#   2. A78's "ensure default MANUAL profile per company" check saw
#      SHOPIFY-NXENTRA-RE and skipped creating AR-DEFAULT.
#   3. A79's customer/vendor backfill bound customers to
#      SHOPIFY-NXENTRA-RE because it was the only "MANUAL" CUSTOMER
#      profile available.
#
# This migration:
#   (a) Flips ShopifyStore-referenced profiles to GATEWAY.
#   (b) Re-runs the "ensure default MANUAL profile" backfill — now that
#       SHOPIFY-* is correctly classified, the check finds zero MANUAL
#       and creates AR-DEFAULT / AP-DEFAULT.
#   (c) Re-binds any Customer / Vendor whose default_posting_profile is
#       now GATEWAY to the company's is_default MANUAL profile.

from django.db import migrations
from django.db.models import Q


def _flip_shopify_store_profiles(apps):
    PostingProfile = apps.get_model("sales", "PostingProfile")
    try:
        ShopifyStore = apps.get_model("shopify_connector", "ShopifyStore")
    except LookupError:
        return  # shopify_connector not installed in this deployment

    profile_ids = set(
        ShopifyStore.objects.exclude(default_posting_profile__isnull=True).values_list(
            "default_posting_profile_id", flat=True
        )
    )
    if profile_ids:
        PostingProfile.objects.filter(id__in=profile_ids, usage="MANUAL").update(usage="GATEWAY")


def _ensure_default_profiles(apps):
    PostingProfile = apps.get_model("sales", "PostingProfile")
    Account = apps.get_model("accounting", "Account")
    Company = apps.get_model("accounts", "Company")

    for company in Company.objects.all():
        _ensure_default(
            PostingProfile, Account, company,
            profile_type="CUSTOMER", account_role="RECEIVABLE_CONTROL",
            code="AR-DEFAULT", name="AR Default", name_ar="ذمم مدينة",
        )
        _ensure_default(
            PostingProfile, Account, company,
            profile_type="VENDOR", account_role="PAYABLE_CONTROL",
            code="AP-DEFAULT", name="AP Default", name_ar="ذمم دائنة",
        )


def _ensure_default(PostingProfile, Account, company, *, profile_type, account_role, code, name, name_ar):
    has_manual = PostingProfile.objects.filter(
        company=company,
        profile_type=profile_type,
        usage="MANUAL",
        is_active=True,
    ).exists()
    if has_manual:
        return

    control = Account.objects.filter(company=company, role=account_role).first()
    if control is None:
        return

    final_code = code
    suffix = 2
    while PostingProfile.objects.filter(company=company, code=final_code).exists():
        final_code = f"{code}-{suffix}"
        suffix += 1

    has_any_default = PostingProfile.objects.filter(
        company=company, profile_type=profile_type, is_default=True
    ).exists()

    PostingProfile.objects.create(
        company=company,
        code=final_code,
        name=name,
        name_ar=name_ar,
        description="Auto-created default for manual document entry (A79 follow-up migration).",
        profile_type=profile_type,
        usage="MANUAL",
        control_account=control,
        is_default=not has_any_default,
        is_active=True,
    )


def _rebind_customers_and_vendors(apps):
    PostingProfile = apps.get_model("sales", "PostingProfile")
    Customer = apps.get_model("accounting", "Customer")
    Vendor = apps.get_model("accounting", "Vendor")

    _rebind_one_type(
        Customer, PostingProfile, profile_type="CUSTOMER",
    )
    _rebind_one_type(
        Vendor, PostingProfile, profile_type="VENDOR",
    )


def _rebind_one_type(Model, PostingProfile, *, profile_type):
    # Records whose current default_posting_profile is now GATEWAY (or NULL
    # because A79 couldn't resolve one) need to be repointed at the company's
    # is_default MANUAL profile, which A78d/this migration just created.
    stale = Model.objects.filter(
        Q(default_posting_profile__usage="GATEWAY") | Q(default_posting_profile__isnull=True)
    )
    for record in stale.distinct():
        target = (
            PostingProfile.objects.filter(
                company=record.company,
                profile_type=profile_type,
                usage="MANUAL",
                is_active=True,
                is_default=True,
            )
            .order_by("id")
            .first()
        )
        if target is None:
            target = (
                PostingProfile.objects.filter(
                    company=record.company,
                    profile_type=profile_type,
                    usage="MANUAL",
                    is_active=True,
                )
                .order_by("id")
                .first()
            )
        if target is not None:
            record.default_posting_profile = target
            record.save(update_fields=["default_posting_profile"])


def _run(apps, schema_editor):
    _flip_shopify_store_profiles(apps)
    _ensure_default_profiles(apps)
    _rebind_customers_and_vendors(apps)


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0012_postingprofile_usage"),
        ("accounting", "0033_customer_vendor_default_posting_profile"),
        # Need ShopifyStore.default_posting_profile to exist in the historical
        # model state. 0010 added it; depending on it via swappable dep ensures
        # the migration only runs after that field is present.
        ("shopify_connector", "0010_add_module_routing_fields"),
    ]

    operations = [
        migrations.RunPython(_run, _noop_reverse),
    ]
