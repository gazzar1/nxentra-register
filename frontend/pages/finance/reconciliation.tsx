import { useEffect, useMemo, useState } from "react";
import type { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  Wallet,
  Truck,
  Building2,
  HelpCircle,
  AlertCircle,
  Loader2,
  ChevronRight,
  ChevronDown,
  RefreshCw,
} from "lucide-react";

import { AppLayout } from "@/components/layout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  reconciliationService,
  type AgingBucket,
  type ProviderType,
  type ReconciliationDrilldown,
  type ReconciliationProviderRow,
  type ReconciliationSummary,
} from "@/services/reconciliation.service";

const PROVIDER_ICON: Record<ProviderType, JSX.Element> = {
  gateway: <Wallet className="h-4 w-4" />,
  courier: <Truck className="h-4 w-4" />,
  bank_transfer: <Building2 className="h-4 w-4" />,
  manual: <HelpCircle className="h-4 w-4" />,
  marketplace: <Wallet className="h-4 w-4" />,
};

const AGING_LABEL: Record<AgingBucket, string> = {
  none: "—",
  "0_7d": "0–7 days",
  "7_30d": "7–30 days",
  "30_plus": "30+ days",
};

const AGING_VARIANT: Record<AgingBucket, "secondary" | "warning" | "destructive" | "outline"> = {
  none: "outline",
  "0_7d": "secondary",
  "7_30d": "warning",
  "30_plus": "destructive",
};

function formatMoney(s: string): string {
  // Server returns "150.00" already 2dp. Pretty-print with thousands separator.
  const n = Number(s);
  if (!Number.isFinite(n)) return s;
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export default function ReconciliationPage() {
  const { toast } = useToast();

  const [summary, setSummary] = useState<ReconciliationSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [drilldownByProvider, setDrilldownByProvider] = useState<
    Record<number, ReconciliationDrilldown | null>
  >({});
  const [expandedProvider, setExpandedProvider] = useState<number | null>(null);
  const [drilldownLoading, setDrilldownLoading] = useState<number | null>(null);

  const fetchSummary = async (showSpinner = true) => {
    if (showSpinner) setLoading(true);
    else setRefreshing(true);
    try {
      const { data } = await reconciliationService.summary();
      setSummary(data);
    } catch {
      toast({
        title: "Failed to load reconciliation summary.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchSummary();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleToggleProvider = async (row: ReconciliationProviderRow) => {
    if (!row.provider_id) return; // can't drill into a row without a provider
    if (expandedProvider === row.provider_id) {
      setExpandedProvider(null);
      return;
    }
    setExpandedProvider(row.provider_id);
    if (drilldownByProvider[row.provider_id]) return;
    setDrilldownLoading(row.provider_id);
    try {
      const { data } = await reconciliationService.drilldown(row.provider_id, row.account_id);
      setDrilldownByProvider((prev) => ({
        ...prev,
        [row.provider_id as number]: data,
      }));
    } catch {
      toast({
        title: `Failed to load ${row.provider_name} drilldown.`,
        variant: "destructive",
      });
    } finally {
      setDrilldownLoading(null);
    }
  };

  const stage1Rows = useMemo(() => summary?.stage1.providers ?? [], [summary]);
  const totals = summary?.stage1.totals;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Reconciliation"
          subtitle="Where is my money? — across Shopify, gateways, couriers, and the bank"
        />

        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchSummary(false)}
            disabled={refreshing || loading}
          >
            {refreshing ? (
              <Loader2 className="me-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="me-2 h-4 w-4" />
            )}
            Refresh
          </Button>
        </div>

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : !summary ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No reconciliation data available. Connect a Shopify store to get started.
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Top-line totals */}
            {totals && (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <SummaryTile
                  label="Total Expected"
                  value={formatMoney(totals.total_expected)}
                  caption="From Shopify orders, awaiting settlement"
                />
                <SummaryTile
                  label="Total Settled"
                  value={formatMoney(totals.total_settled)}
                  caption="Already drained from clearing"
                />
                <SummaryTile
                  label="Open Balance"
                  value={formatMoney(totals.open_balance)}
                  caption={`Across ${totals.providers_with_open_balance} provider(s)`}
                  emphasize
                />
                <SummaryTile
                  label="Aged > 30 days"
                  value={formatMoney(totals.aged_30_plus)}
                  caption={
                    Number(totals.aged_30_plus) > 0
                      ? "Needs attention"
                      : "Nothing overdue"
                  }
                  variant={Number(totals.aged_30_plus) > 0 ? "destructive" : "default"}
                />
              </div>
            )}

            {/* Stage 1 — Sales → Clearing */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Wallet className="h-5 w-5" />
                  Stage 1 — Sales → Clearing
                </CardTitle>
              </CardHeader>
              <CardContent>
                {stage1Rows.length === 0 ? (
                  <p className="py-4 text-sm text-muted-foreground italic">
                    No clearing activity yet — once Shopify orders land, each settlement
                    provider appears here with its open balance and aging.
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="border-b text-left text-xs uppercase text-muted-foreground">
                        <tr>
                          <th className="py-2 pr-3">Provider</th>
                          <th className="py-2 pr-3">Account</th>
                          <th className="py-2 pr-3 text-right">Expected</th>
                          <th className="py-2 pr-3 text-right">Settled</th>
                          <th className="py-2 pr-3 text-right">Open Balance</th>
                          <th className="py-2 pr-3">Oldest</th>
                          <th className="py-2 pr-3">Aging</th>
                          <th className="py-2"></th>
                        </tr>
                      </thead>
                      <tbody>
                        {stage1Rows.map((row) => {
                          const isExpanded = expandedProvider === row.provider_id;
                          const drill = row.provider_id
                            ? drilldownByProvider[row.provider_id]
                            : null;
                          return (
                            <ProviderRow
                              key={`${row.account_id}-${row.dimension_value_id}`}
                              row={row}
                              isExpanded={isExpanded}
                              isLoading={drilldownLoading === row.provider_id}
                              drilldown={drill ?? null}
                              onToggle={() => handleToggleProvider(row)}
                            />
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Stage 2 — Clearing → Settlement */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <RefreshCw className="h-5 w-5" />
                  Stage 2 — Clearing → Settlement
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                {summary.stage2.available ? (
                  <>
                    <div className="flex flex-wrap gap-6">
                      <div>
                        <p className="text-xs uppercase text-muted-foreground">Settlements posted</p>
                        <p className="text-lg font-semibold">{summary.stage2.settled_count ?? 0}</p>
                      </div>
                      <div>
                        <p className="text-xs uppercase text-muted-foreground">Settled total</p>
                        <p className="text-lg font-semibold">
                          {formatMoney(summary.stage2.settled_total ?? "0")}
                        </p>
                      </div>
                    </div>
                    {summary.stage2.pending_csv_import_note && (
                      <div className="flex items-start gap-2 rounded-md border border-yellow-500/30 bg-yellow-500/10 p-3 text-xs">
                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-yellow-500" />
                        <span>{summary.stage2.pending_csv_import_note}</span>
                      </div>
                    )}
                  </>
                ) : (
                  <p className="text-muted-foreground italic">
                    Settlement data not available yet.
                  </p>
                )}
              </CardContent>
            </Card>

            {/* Stage 3 — Bank Match */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Building2 className="h-5 w-5" />
                  Stage 3 — Bank Match
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm">
                {summary.stage3.available ? (
                  <div className="flex flex-wrap gap-6">
                    <div>
                      <p className="text-xs uppercase text-muted-foreground">Total bank lines</p>
                      <p className="text-lg font-semibold">{summary.stage3.total_lines ?? 0}</p>
                    </div>
                    <div>
                      <p className="text-xs uppercase text-muted-foreground">Matched</p>
                      <p className="text-lg font-semibold">{summary.stage3.matched_lines ?? 0}</p>
                    </div>
                    <div>
                      <p className="text-xs uppercase text-muted-foreground">Unmatched</p>
                      <p className="text-lg font-semibold">{summary.stage3.unmatched_lines ?? 0}</p>
                    </div>
                  </div>
                ) : (
                  <p className="text-muted-foreground italic">No bank statement lines imported yet.</p>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </AppLayout>
  );
}

// =============================================================================
// Sub-components
// =============================================================================

function SummaryTile({
  label,
  value,
  caption,
  emphasize,
  variant,
}: {
  label: string;
  value: string;
  caption?: string;
  emphasize?: boolean;
  variant?: "default" | "destructive";
}) {
  return (
    <Card
      className={
        variant === "destructive"
          ? "border-destructive/40 bg-destructive/5"
          : emphasize
          ? "border-primary/40"
          : ""
      }
    >
      <CardContent className="space-y-1 py-4">
        <p className="text-xs uppercase text-muted-foreground">{label}</p>
        <p className="text-2xl font-semibold">{value}</p>
        {caption && <p className="text-xs text-muted-foreground">{caption}</p>}
      </CardContent>
    </Card>
  );
}

function ProviderRow({
  row,
  isExpanded,
  isLoading,
  drilldown,
  onToggle,
}: {
  row: ReconciliationProviderRow;
  isExpanded: boolean;
  isLoading: boolean;
  drilldown: ReconciliationDrilldown | null;
  onToggle: () => void;
}) {
  const open = Number(row.open_balance);
  return (
    <>
      <tr
        className="cursor-pointer border-b hover:bg-muted/50"
        onClick={onToggle}
      >
        <td className="py-2 pr-3">
          <div className="flex items-center gap-2">
            {PROVIDER_ICON[row.provider_type]}
            <span className="font-medium">{row.provider_name}</span>
            {row.needs_review && <Badge variant="warning">Review</Badge>}
          </div>
        </td>
        <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">
          {row.account_code}
        </td>
        <td className="py-2 pr-3 text-right">{formatMoney(row.total_debit)}</td>
        <td className="py-2 pr-3 text-right">{formatMoney(row.total_credit)}</td>
        <td className={`py-2 pr-3 text-right font-semibold ${open > 0 ? "" : "text-muted-foreground"}`}>
          {formatMoney(row.open_balance)}
        </td>
        <td className="py-2 pr-3 text-xs text-muted-foreground">
          {row.oldest_entry_date ?? "—"}
        </td>
        <td className="py-2 pr-3">
          <Badge variant={AGING_VARIANT[row.aging_bucket]}>
            {AGING_LABEL[row.aging_bucket]}
          </Badge>
        </td>
        <td className="py-2 text-right">
          {isExpanded ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
        </td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={8} className="bg-muted/30 px-3 py-3">
            {isLoading ? (
              <div className="flex items-center justify-center py-4">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            ) : drilldown && drilldown.lines.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="border-b text-left uppercase text-muted-foreground">
                    <tr>
                      <th className="py-1 pr-3">Date</th>
                      <th className="py-1 pr-3">Entry</th>
                      <th className="py-1 pr-3">Description</th>
                      <th className="py-1 pr-3 text-right">Debit</th>
                      <th className="py-1 pr-3 text-right">Credit</th>
                      <th className="py-1 pr-3 text-right">Running</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drilldown.lines.map((l) => (
                      <tr key={l.id} className="border-b last:border-0">
                        <td className="py-1 pr-3">{l.date}</td>
                        <td className="py-1 pr-3 font-mono">{l.entry_number}</td>
                        <td className="py-1 pr-3">{l.description}</td>
                        <td className="py-1 pr-3 text-right">
                          {Number(l.debit) > 0 ? formatMoney(l.debit) : "—"}
                        </td>
                        <td className="py-1 pr-3 text-right">
                          {Number(l.credit) > 0 ? formatMoney(l.credit) : "—"}
                        </td>
                        <td className="py-1 pr-3 text-right font-semibold">
                          {formatMoney(l.running_balance)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="py-2 text-xs text-muted-foreground italic">
                No lines for this provider.
              </p>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
