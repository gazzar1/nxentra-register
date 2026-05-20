# A78: PostingProfile.usage — distinguishes MANUAL profiles (shown in
# invoice/bill dropdowns) from GATEWAY profiles (owned by platform
# connectors, hidden from manual entry).
#
# Data step:
#   1. Any profile that is the FK target of a SettlementProvider is flipped
#      to GATEWAY. These were bootstrapped by shopify_connector and route
#      to clearing accounts with REQUIRED dimension rules — manual entry
#      can't satisfy them and would error at JE post time.
#   2. For each company that ends up with zero MANUAL profiles of a given
#      profile_type, auto-create a default one pointing at the company's
#      RECEIVABLE_CONTROL (for CUSTOMER) or PAYABLE_CONTROL (for VENDOR)
#      account. Without this, companies that only have Shopify-created
#      profiles (e.g. demo company "Shopify_R") would have an empty
#      dropdown after the filter lands.

from django.db import migrations, models


def _backfill_usage(apps, schema_editor):
    PostingProfile = apps.get_model("sales", "PostingProfile")
    SettlementProvider = apps.get_model("accounting", "SettlementProvider")
    Account = apps.get_model("accounting", "Account")
    Company = apps.get_model("accounts", "Company")

    # Step 1: flip platform-routed profiles to GATEWAY.
    gateway_profile_ids = set(
        SettlementProvider.objects.values_list("posting_profile_id", flat=True)
    )
    if gateway_profile_ids:
        PostingProfile.objects.filter(id__in=gateway_profile_ids).update(usage="GATEWAY")

    # Step 2: ensure each company has a MANUAL CUSTOMER + MANUAL VENDOR
    # profile. Backfill is best-effort: skip the company if it has no
    # RECEIVABLE/PAYABLE_CONTROL account yet (chart not seeded — they'll
    # create profiles themselves later).
    for company in Company.objects.all():
        _ensure_default(
            PostingProfile,
            Account,
            company,
            profile_type="CUSTOMER",
            account_role="RECEIVABLE_CONTROL",
            code="AR-DEFAULT",
            name="AR Default",
            name_ar="ذمم مدينة",
        )
        _ensure_default(
            PostingProfile,
            Account,
            company,
            profile_type="VENDOR",
            account_role="PAYABLE_CONTROL",
            code="AP-DEFAULT",
            name="AP Default",
            name_ar="ذمم دائنة",
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
        # Chart not seeded for this company; user will create profiles
        # themselves once they add an AR/AP account.
        return

    # If the desired code is taken (e.g. by a deactivated row), suffix it.
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
        description="Auto-created default for manual document entry (A78 migration).",
        profile_type=profile_type,
        usage="MANUAL",
        control_account=control,
        is_default=not has_any_default,
        is_active=True,
    )


def _noop_reverse(apps, schema_editor):
    # Reversing the data step would mean guessing which auto-created rows
    # to delete; not worth the risk. The schema reverse drops the column,
    # which is enough to roll back if needed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0032_strip_company_id_from_je_entry_numbers"),
        ("accounts", "0001_initial"),
        ("sales", "0011_add_line_warehouse"),
    ]

    operations = [
        migrations.AddField(
            model_name="postingprofile",
            name="usage",
            field=models.CharField(
                choices=[("MANUAL", "Manual entry"), ("GATEWAY", "Platform / gateway")],
                default="MANUAL",
                help_text=(
                    "MANUAL: shown in invoice/bill dropdowns for human entry. "
                    "GATEWAY: owned by a connector (e.g. Shopify), hidden from manual "
                    "dropdowns and rejected if used to create a manual document."
                ),
                max_length=10,
            ),
        ),
        migrations.RunPython(_backfill_usage, _noop_reverse),
    ]
