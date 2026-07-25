// A4: constrained-pilot UI scoping.
//
// Backend runtime gates are the safety guarantee; hiding these surfaces is a UX
// courtesy so a pilot founder is not led into dead ends (a click would 403 or
// structurally skip). Keep this list aligned with accounts.pilot_policy.
export const ISOLATED_SHADOW_LEDGER_V1 = "ISOLATED_SHADOW_LEDGER_V1";

export function isConstrainedPilot(pilotProfile?: string | null): boolean {
  return pilotProfile === ISOLATED_SHADOW_LEDGER_V1;
}

// Nav hrefs hidden under the constrained pilot — each corresponds to a blocked
// capability: inventory (Option B), Stripe, legacy banking, projection rebuild.
export const PILOT_HIDDEN_HREF_PREFIXES = [
  "/inventory",
  "/stripe",
  "/banking",
  "/admin/projections",
];

export function isPilotHiddenHref(href: string): boolean {
  return PILOT_HIDDEN_HREF_PREFIXES.some((p) => href === p || href.startsWith(p + "/"));
}
