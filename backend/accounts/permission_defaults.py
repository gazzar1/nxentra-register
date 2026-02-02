# accounts/permission_defaults.py

ROLE_DEFAULTS = {
    "OWNER": {
        # Company / security
        "company.switch",
        "company.view",
        "company.manage_settings",
        "company.manage_users",
        "company.manage_permissions",
        "company.invite_users",

        # Accounting
        "accounts.view",
        "accounts.manage",
        "journal.view",
        "journal.create",
        "journal.edit_draft",
        "journal.post",
        "journal.reverse",

        # Periods
        "periods.view",
        "periods.close",
        "periods.reopen",
        "periods.configure",

        # Reports
        "reports.view",
        "reports.export",

        # EDIM (External Data Ingestion & Mapping)
        "edim.view",
        "edim.manage_sources",
        "edim.manage_mappings",
        "edim.manage_crosswalks",
        "edim.stage_data",
        "edim.review_batches",
        "edim.commit_batches",
    },
    "ADMIN": {
        "company.switch",
        "company.view",
        "company.manage_users",
        "company.manage_permissions",
        "company.invite_users",

        "accounts.view",
        "accounts.manage",
        "journal.view",
        "journal.create",
        "journal.edit_draft",
        "journal.post",
        "journal.reverse",

        "periods.view",
        "reports.view",
        "reports.export",

        # EDIM (External Data Ingestion & Mapping)
        "edim.view",
        "edim.manage_sources",
        "edim.manage_mappings",
        "edim.manage_crosswalks",
        "edim.stage_data",
        "edim.review_batches",
        "edim.commit_batches",
    },
    "USER": {
        "company.switch",
        "company.view",

        "accounts.view",
        "journal.view",
        "journal.create",
        "journal.edit_draft",

        "periods.view",
        "reports.view",

        # EDIM (External Data Ingestion & Mapping)
        "edim.view",
        "edim.stage_data",
    },
    "VIEWER": {
        "company.switch",
        "company.view",

        "accounts.view",
        "journal.view",

        "periods.view",
        "reports.view",

        # EDIM (External Data Ingestion & Mapping)
        "edim.view",
    },
}

def all_permission_codes() -> set[str]:
    codes: set[str] = set()
    for s in ROLE_DEFAULTS.values():
        codes |= set(s)
    return codes
