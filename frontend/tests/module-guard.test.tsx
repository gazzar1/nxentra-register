/**
 * F3 — ModuleGuard messaging for a disabled module.
 *
 * The App-Store install lands the OWNER on a module route (e.g. /shopify/settings)
 * mid-onboarding, before the setup wizard has enabled the module. The guard used
 * to dead-end everyone with "Module Not Enabled — contact your administrator",
 * shown to the very owner who just signed up. Now it's actionable:
 *   - onboarding incomplete  → "Finish setup" → /onboarding/setup
 *   - owner, onboarding done  → "Enable in Settings" → /settings/modules
 *   - non-owner, done         → the original "contact your administrator"
 * Enabled modules and core routes always pass through.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

type Company = { onboarding_completed?: boolean } | null;
type Membership = { role: string } | null;

let mockModules: Array<{ key: string; is_enabled: boolean; is_core: boolean }> = [];
let mockCompany: Company = null;
let mockMembership: Membership = null;
let mockPathname = "/shopify/settings";
const push = vi.fn();

vi.mock("@/queries/useModules", () => ({
  useModules: () => ({ data: mockModules, isLoading: false }),
}));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ company: mockCompany, membership: mockMembership }),
}));
vi.mock("next-i18next", () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
}));
vi.mock("next/router", () => ({
  useRouter: () => ({ pathname: mockPathname, push }),
}));

import { ModuleGuard } from "@/components/layout/ModuleGuard";

const shopifyDisabled = [{ key: "shopify_connector", is_enabled: false, is_core: false }];
const shopifyEnabled = [{ key: "shopify_connector", is_enabled: true, is_core: false }];

function renderGuard() {
  return render(
    <ModuleGuard>
      <div>PROTECTED</div>
    </ModuleGuard>,
  );
}

describe("ModuleGuard (F3)", () => {
  beforeEach(() => {
    push.mockClear();
    mockModules = shopifyDisabled;
    mockCompany = { onboarding_completed: true };
    mockMembership = { role: "USER" };
    mockPathname = "/shopify/settings";
  });

  it("renders children on a core (unmapped) route", () => {
    mockPathname = "/dashboard";
    renderGuard();
    expect(screen.getByText("PROTECTED")).toBeInTheDocument();
  });

  it("renders children when the module is enabled", () => {
    mockModules = shopifyEnabled;
    renderGuard();
    expect(screen.getByText("PROTECTED")).toBeInTheDocument();
  });

  it("onboarding incomplete → Finish setup CTA to the wizard, not 'contact administrator'", async () => {
    mockCompany = { onboarding_completed: false };
    mockMembership = { role: "OWNER" };
    renderGuard();

    expect(screen.queryByText("PROTECTED")).not.toBeInTheDocument();
    expect(screen.getByText(/Finish setting up/i)).toBeInTheDocument();
    expect(screen.queryByText(/contact your administrator/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Finish setup/i }));
    expect(push).toHaveBeenCalledWith("/onboarding/setup");
  });

  it("owner with onboarding done → Enable in Settings, not 'contact administrator'", async () => {
    mockMembership = { role: "OWNER" };
    renderGuard();

    expect(screen.queryByText(/contact your administrator/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Enable in Settings/i }));
    expect(push).toHaveBeenCalledWith("/settings/modules");
  });

  it("non-owner with onboarding done → the original contact-administrator message", async () => {
    mockMembership = { role: "USER" };
    renderGuard();

    expect(screen.getByText(/contact your administrator/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Back to Dashboard/i }));
    expect(push).toHaveBeenCalledWith("/dashboard");
  });
});
