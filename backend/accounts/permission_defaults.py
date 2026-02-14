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

        # Inventory
        "inventory.warehouse.create",
        "inventory.warehouse.update",
        "inventory.stock.receive",
        "inventory.stock.issue",
        "inventory.adjustment.create",
        "inventory.opening_balance.create",

        # Periods
        "periods.view",
        "periods.close",
        "periods.reopen",
        "periods.configure",

        # Reports
        "reports.view",
        "reports.export",

        # Voice (admin can grant/revoke access and manage quotas)
        "voice.admin",
        "voice.view_usage",

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

        # Inventory
        "inventory.warehouse.create",
        "inventory.warehouse.update",
        "inventory.stock.receive",
        "inventory.stock.issue",
        "inventory.adjustment.create",
        "inventory.opening_balance.create",

        "periods.view",
        "reports.view",
        "reports.export",

        # Voice (admin can grant/revoke access and manage quotas)
        "voice.admin",
        "voice.view_usage",

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

        # Inventory (limited)
        "inventory.stock.receive",
        "inventory.stock.issue",
        "inventory.adjustment.create",

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
