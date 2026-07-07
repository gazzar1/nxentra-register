import Link from "next/link";
import { CalendarClock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { findPriorOpenPeriod, usePeriods } from "@/queries/usePeriods";

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/**
 * A152 item 6 — conditional month-end nudge, NOT a permanent period chip.
 *
 * Renders NOTHING unless actionable: the most recently ended NORMAL period is
 * still OPEN and we're ≥ N days into the next one (e.g. "June still open" on
 * July 4th). Deep-links to the guided Month-End Close WITH the period's
 * year/month so the landing page shows the month the chip named (review C2).
 * Mirrors NotificationBell's conditional-render idiom; a static "current
 * period" chip was explicitly rejected as non-actionable wallpaper.
 */
export function PeriodCloseChip() {
  const { data: periods } = usePeriods();
  const prior = findPriorOpenPeriod(periods ?? []);
  if (!prior) return null;

  const [y, m] = prior.period.end_date.split("-").map(Number);
  if (!y || !m) return null;
  const monthName = MONTH_NAMES[m - 1];
  const currentYear = new Date().getFullYear();
  const label = y === currentYear ? `${monthName} still open` : `${monthName} ${y} still open`;

  return (
    <Link href={`/settings/month-end-close?year=${y}&month=${m}`} className="hidden md:block">
      <Badge variant="warning" className="cursor-pointer gap-1 whitespace-nowrap hover:opacity-80">
        <CalendarClock className="h-3 w-3" />
        {label}
      </Badge>
    </Link>
  );
}
