# accounting/mappings.py
"""
Reusable account-role mapping for vertical modules.

Each vertical module (property, clinic, ecommerce, etc.) needs to map
business-specific account roles (RENTAL_INCOME, PATIENT_RECEIVABLE, etc.)
to GL accounts from the Chart of Accounts.

This model replaces per-module one-off mapping models with a single
generic table that all verticals share.

Usage in a projection:
    mapping = ModuleAccountMapping.get_mapping(company, "properties")
    ar = mapping.get("ACCOUNTS_RECEIVABLE")
    revenue = mapping.get("RENTAL_INCOME")

Usage for a single role:
    account = ModuleAccountMapping.get_account(company, "clinic", "CASH_BANK")
"""

from __future__ import annotations

import logging

from django.db import models

from accounting.models import Account
from accounts.models import Company
from projections.write_barrier import write_context_allowed

logger = logging.getLogger(__name__)


# Canonical ModuleAccountMapping module key per payment/settlement provider.
#
# Generic platform connectors (Stripe first; future WooCommerce/Amazon) resolve
# "platform_{slug}" — matching PlatformAccountingProjection — so a provider's
# order/refund/dispute JEs and its settlement JEs land on ONE mapping. Shopify
# (and the gateways/couriers that settle *within* the Shopify store — paymob and
# bosta ride external_system='shopify') keep the grandfathered "shopify_connector"
# key, seeded by _setup_shopify_accounts and read by the shopify_accounting
# projection.
#
# Single source for a key that was previously computed three inconsistent ways
# ("platform_{slug}" for order JEs vs "{ext}_connector" for settlement JEs vs a
# hardcoded "shopify_connector"). See ADR-0002 and test_stripe_module_key_gate.py.
_PROVIDER_MODULE_OVERRIDES = {
    "shopify": "shopify_connector",
}


def module_key_for_provider(provider: str) -> str:
    """Canonical ModuleAccountMapping module key for a payment/settlement
    provider, given its external_system / platform_slug (the same canonical
    string for a given provider, e.g. 'stripe', 'shopify')."""
    key = (provider or "").strip().lower()
    return _PROVIDER_MODULE_OVERRIDES.get(key, f"platform_{key}")


class ModuleAccountMapping(models.Model):
    """
    Maps an account role within a module to a GL Account for a company.

    Unique per (company, module, role). Each vertical declares the roles
    it needs in its AppConfig.account_roles list.

    Write protection: uses the same write-barrier pattern as other
    configuration models. Allowed contexts: command, projection,
    bootstrap, migration (and TESTING mode).
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="module_account_mappings",
    )
    module = models.CharField(
        max_length=50,
        help_text="App label of the vertical module, e.g. 'properties', 'clinic'.",
    )
    role = models.CharField(
        max_length=50,
        help_text="Account role within the module, e.g. 'RENTAL_INCOME', 'CASH_BANK'.",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("company", "module", "role")
        ordering = ("module", "role")
        verbose_name = "Module Account Mapping"
        verbose_name_plural = "Module Account Mappings"

    def __str__(self):
        acct = self.account.code if self.account else "(unmapped)"
        return f"{self.module}.{self.role} -> {acct}"

    # ------------------------------------------------------------------
    # Write protection
    # ------------------------------------------------------------------

    _ALLOWED_WRITE_CONTEXTS = {"command", "projection", "bootstrap", "migration"}

    def save(self, *args, **kwargs):
        from django.conf import settings

        if not write_context_allowed(self._ALLOWED_WRITE_CONTEXTS) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "ModuleAccountMapping is a configuration model. "
                "Direct saves are only allowed within command/projection/bootstrap/migration contexts."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.conf import settings

        if not write_context_allowed(self._ALLOWED_WRITE_CONTEXTS) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "ModuleAccountMapping is a configuration model. "
                "Direct deletes are only allowed within an allowed write context."
            )
        return super().delete(*args, **kwargs)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_mapping(cls, company: Company, module: str) -> dict[str, Account | None]:
        """
        Return {role: account_or_None} for all roles of a module.

        Example:
            mapping = ModuleAccountMapping.get_mapping(company, "properties")
            ar = mapping.get("ACCOUNTS_RECEIVABLE")
        """
        qs = cls.objects.filter(
            company=company,
            module=module,
        ).select_related("account")
        return {m.role: m.account for m in qs}

    @classmethod
    def get_account(cls, company: Company, module: str, role: str) -> Account | None:
        """Get a single account by module and role, or None if unmapped."""
        try:
            return (
                cls.objects.select_related("account")
                .get(
                    company=company,
                    module=module,
                    role=role,
                )
                .account
            )
        except cls.DoesNotExist:
            return None

    @classmethod
    def get_accounts_for_role(cls, company: Company, role: str) -> list[Account]:
        """All distinct accounts mapped to ``role`` across EVERY module for the
        company.

        Used where a role is seeded per-provider (e.g. EXPECTED_BANK_DEPOSIT
        lives under ``platform_stripe`` for Stripe and ``shopify_connector`` for
        Shopify) and the caller must consider all providers' accounts, not just
        one module's — e.g. the bank-match candidate picker, which must surface
        an unreconciled deposit regardless of which provider settled it
        (ADR-0002 per-provider EBD).
        """
        seen: dict[int, Account] = {}
        for m in cls.objects.filter(company=company, role=role).select_related("account"):
            if m.account_id and m.account_id not in seen:
                seen[m.account_id] = m.account
        return list(seen.values())

    @classmethod
    def check_required_roles(
        cls,
        company: Company,
        module: str,
        required_roles: list[str],
    ) -> list[str]:
        """
        Return list of roles that are missing or have no account assigned.
        Empty list means all required roles are mapped.
        """
        mapping = cls.get_mapping(company, module)
        missing = []
        for role in required_roles:
            account = mapping.get(role)
            if account is None:
                missing.append(role)
        return missing
