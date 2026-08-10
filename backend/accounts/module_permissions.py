# accounts/module_permissions.py
"""
DRF permission class for module-level access control.

Checks that the requesting user's company has the relevant module enabled.
Core modules always pass. Optional modules require a CompanyModule record
with is_enabled=True.

Usage:
    class MyView(APIView):
        permission_classes = [IsAuthenticated, ModuleEnabled]
        module_key = "clinic"
"""

from rest_framework.permissions import BasePermission

from accounts.authz import resolve_actor
from accounts.models import CompanyModule
from accounts.module_registry import ModuleCategory, module_registry
from accounts.pilot_policy import capability_for_module, require_supported


class ModuleEnabled(BasePermission):
    """
    Deny access if the view's module is disabled for the tenant.

    - Views without module_key: always allowed (backwards compatible).
    - Core modules: always allowed (no DB check).
    - Pilot-gated modules: a pilot profile that forbids the module's capability
      raises PilotScopeBlocked (403) BEFORE the enablement lookup, so a stale
      is_enabled=True row cannot reopen the module.
    - Optional modules: allowed only if CompanyModule.is_enabled=True.
    """

    message = "This module is not enabled for your company."

    def has_permission(self, request, view):
        module_key = getattr(view, "module_key", None)
        if not module_key:
            return True

        # Core modules are always enabled
        mod = module_registry.get(module_key)
        if mod and mod["category"] == ModuleCategory.CORE:
            return True

        # Resolve actor to get company
        try:
            actor = resolve_actor(request)
        except Exception:
            # If actor resolution fails, let other permissions handle it. The
            # runtime command gates remain load-bearing (this fail-open is a
            # stated A4 decision — it never widens a pilot's surface because the
            # command layer re-checks the capability).
            return True

        if not actor.company:
            return True

        # A4: pilot-aware — raise the stable PilotScopeBlocked BEFORE the
        # enablement lookup so a pilot-forbidden module cannot be reopened via a
        # stale/drifted CompanyModule row. Unmapped modules and NONE-profile
        # companies are unaffected (require_supported is a no-op for them).
        capability = capability_for_module(module_key)
        if capability is not None:
            require_supported(actor.company, capability)

        return CompanyModule.objects.filter(
            company=actor.company,
            module_key=module_key,
            is_enabled=True,
        ).exists()
