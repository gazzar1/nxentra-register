import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/contexts/AuthContext";
import { periodsService, type FiscalPeriod } from "@/services/periods.service";

// A152 items 5+6 — one shared, cached read of the company's fiscal periods.
// Pre-A152 every form fetched periods ad-hoc in a useEffect (payments,
// receipts, JE form) and the header had no period data at all. This hook
// dedupes those reads behind react-query (mirrors useNotifications) so
// CompanyDateInput's opt-in warning and the header chip cost one request.

export const periodKeys = {
  all: (companyId: number | string | undefined) => ["fiscal-periods", companyId ?? "none"] as const,
};

export function usePeriods(options?: { enabled?: boolean }) {
  const { company } = useAuth();
  return useQuery({
    queryKey: periodKeys.all(company?.id),
    queryFn: async () => {
      const { data } = await periodsService.list();
      return data.periods ?? [];
    },
    enabled: (options?.enabled ?? true) && !!company,
    staleTime: 5 * 60_000, // periods change rarely; a close invalidates on reload
  });
}

export interface ResolvedPeriodLabel {
  text: string;
  isProblem: boolean; // closed or no fiscal period possible — render destructive
}

/** Parse an ISO YYYY-MM-DD as a LOCAL date (new Date("YYYY-MM-DD") parses as
 * UTC midnight, which lands on the previous local day in UTC-positive
 * timezones — Egypt included — skewing period-boundary comparisons).
 * setFullYear avoids the Date(y,...) constructor mapping years 0–99 to 19xx. */
function localDate(iso: string): Date | null {
  const [y, m, d] = (iso || "").split("-").map(Number);
  if (!y || !m || !d) return null;
  const parsed = new Date(y, m - 1, d);
  parsed.setFullYear(y);
  return isNaN(parsed.getTime()) ? null : parsed;
}

/** A152 item 5 — resolve the fiscal period a date falls in (logic lifted from
 * payments/receipts). Three no-match cases are distinguished (review C3/C22):
 * - implausible year (mid-typing in DD/MM/YYYY formats) → null, no flicker;
 * - in the backend's auto-provision range → neutral note (the post will
 *   succeed; the year's periods are created on demand — A152 item 4);
 * - outside it → destructive (the post will be refused). */
export function resolvePeriodLabel(isoDate: string, periods: FiscalPeriod[]): ResolvedPeriodLabel | null {
  if (!isoDate || periods.length === 0) return null;
  const d = localDate(isoDate);
  if (!d) return null;
  const match = periods.find((p) => {
    const start = localDate(p.start_date);
    const end = localDate(p.end_date);
    return !!start && !!end && d >= start && d <= end && p.period_type === "NORMAL";
  });
  if (match) {
    const closed = match.status !== "OPEN";
    return {
      text: `Period ${match.period} (${match.start_date} — ${match.end_date})${closed ? " ⚠ CLOSED" : ""}`,
      isProblem: closed,
    };
  }
  const year = d.getFullYear();
  // Mid-typing years ("0002" while entering 15/06/2026) — say nothing yet.
  if (year < 1900) return null;
  // Approximation of the backend sane range (10y back / 1y forward; the
  // fiscal-start offset can shift the boundary year by one — acceptable for a
  // hint). In range: the backend auto-provisions and accepts.
  const thisYear = new Date().getFullYear();
  if (year >= thisYear - 10 && year <= thisYear + 1) {
    return { text: "No period configured yet — it will be created automatically", isProblem: false };
  }
  return { text: "No fiscal period for this date", isProblem: true };
}

// A152 item 6: the header chip appears only when actionable — the most
// recently ended NORMAL period is still OPEN and we're at least this many
// days past its end (e.g. "June still open" from July 4th).
export const PRIOR_PERIOD_NUDGE_DAYS = 3;

export interface PriorOpenPeriod {
  period: FiscalPeriod;
  daysPastEnd: number;
}

/** The MOST RECENTLY ENDED open NORMAL period, ≥ N days past its end — the
 * month whose close ritual is due now. (Review C2/C12: not the oldest across
 * all years — companies seed a whole year of OPEN periods, so "oldest" would
 * pin the chip to last January forever and disagree with the Month-End Close
 * page's previous-month default.) Null when nothing is due. */
export function findPriorOpenPeriod(
  periods: FiscalPeriod[],
  today: Date = new Date(),
  nudgeDays: number = PRIOR_PERIOD_NUDGE_DAYS
): PriorOpenPeriod | null {
  const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  let latest: PriorOpenPeriod | null = null;
  for (const p of periods) {
    if (p.period_type !== "NORMAL" || p.status !== "OPEN") continue;
    const end = localDate(p.end_date);
    if (!end) continue;
    const daysPastEnd = Math.floor((midnight.getTime() - end.getTime()) / 86_400_000);
    if (daysPastEnd < nudgeDays) continue;
    if (!latest || end > localDate(latest.period.end_date)!) {
      latest = { period: p, daysPastEnd };
    }
  }
  return latest;
}
