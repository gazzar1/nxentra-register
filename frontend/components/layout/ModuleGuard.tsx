import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import { ShieldOff } from "lucide-react";
import { useModules, type ModuleInfo } from "@/queries/useModules";

/**
 * Map of route prefixes to module keys.
 * Routes not listed here are always accessible (core modules).
 */
const ROUTE_MODULE_MAP: Record<string, string> = {
  "/clinic": "clinic",
  "/properties": "properties",
  "/inventory": "inventory",
  "/shopify": "shopify_connector",
  "/stripe": "stripe_connector",
  "/banking": "bank_connector",
};

/**
 * Derives enabled module keys from the dedicated /api/modules/ endpoint.
 * This is the authoritative source of truth for module state — not the sidebar.
 */
export function useEnabledModules(): {
  enabledKeys: Set<string>;
  isLoading: boolean;
} {
  const { data: modules, isLoading } = useModules();
  const enabledKeys = new Set(
    modules?.filter((m: ModuleInfo) => m.is_core || m.is_enabled).map((m: ModuleInfo) => m.key) ?? [],
  );
  return { enabledKeys, isLoading };
}

/**
 * Returns the module_key required for the current route, or null if the
 * route belongs to a core module (always accessible).
 */
function getRequiredModule(pathname: string): string | null {
  for (const [prefix, moduleKey] of Object.entries(ROUTE_MODULE_MAP)) {
    if (pathname === prefix || pathname.startsWith(prefix + "/")) {
      return moduleKey;
    }
  }
  return null;
}

interface ModuleGuardProps {
  children: React.ReactNode;
}

/**
 * Blocks rendering of children if the current route's module is disabled
 * for the tenant. Shows a friendly "module not enabled" message instead.
 *
 * Uses /api/modules/ as the authoritative source of module state.
 * Core routes and routes not mapped to a module always pass through.
 */
export function ModuleGuard({ children }: ModuleGuardProps) {
  const router = useRouter();
  const { t } = useTranslation("common");
  const { enabledKeys, isLoading } = useEnabledModules();

  const requiredModule = getRequiredModule(router.pathname);

  // No module check needed for core routes
  if (!requiredModule) {
    return <>{children}</>;
  }

  // While module data is loading, don't block (avoid flash)
  if (isLoading) {
    return <>{children}</>;
  }

  // Module is enabled — render normally
  if (enabledKeys.has(requiredModule)) {
    return <>{children}</>;
  }

  // Module is disabled — show blocked message
  return (
    <div className="flex flex-1 items-center justify-center">
      <div className="mx-auto max-w-md text-center">
        <ShieldOff className="mx-auto h-16 w-16 text-muted-foreground/50" />
        <h2 className="mt-4 text-xl font-semibold">
          {t("moduleGuard.title", "Module Not Enabled")}
        </h2>
        <p className="mt-2 text-muted-foreground">
          {t(
            "moduleGuard.description",
            "This module is not enabled for your company. Contact your administrator to enable it."
          )}
        </p>
        <button
          onClick={() => router.push("/dashboard")}
          className="mt-6 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-accent/90 transition-colors"
        >
          {t("moduleGuard.backToDashboard", "Back to Dashboard")}
        </button>
      </div>
    </div>
  );
}
