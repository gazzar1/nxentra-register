import { AlertTriangle } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";

/**
 * A4: an explicit constrained-pilot notice shown while a company is on the
 * ISOLATED_SHADOW_LEDGER_V1 profile. Backend enforcement is authoritative — this
 * banner just tells the founder what is intentionally out of scope so they are
 * not led into dead ends. Deliberately minimal (not a GA onboarding system).
 */
export function ConstrainedPilotNotice() {
  const { company } = useAuth();
  if (company?.pilot_profile !== "ISOLATED_SHADOW_LEDGER_V1") return null;

  return (
    <div className="mb-4 rounded-md border border-amber-400/60 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-200">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <div>
          <p className="font-medium">Constrained shadow-ledger pilot</p>
          <p className="mt-0.5 text-amber-800 dark:text-amber-300/90">
            This deployment runs a supervised money-movement proof, not statutory
            books. Out of scope: inventory / COGS / gross margin, Stripe and
            Shopify Payments payout accounting, the legacy banking module, and
            projection rebuild. Only the supported Shopify order/refund,
            Paymob/Bosta CSV and canonical bank-CSV workflows are available.
          </p>
        </div>
      </div>
    </div>
  );
}
