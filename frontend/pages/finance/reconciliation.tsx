import { Fragment, useEffect, useMemo, useState } from "react";
import type { GetServerSideProps } from "next";
import Link from "next/link";
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
  ScrollText,
  ClipboardCheck,
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
  type DifferenceReason,
  type ExceptionSeverity,
  type MoneyFlow,
  type MoneyTrace,
  type NeedsReviewItem,
  type OrderReconciliationStatus,
  type ProviderType,
  type ReconciliationDrilldown,
  type ReconciliationOrders,
  type ReconciliationProviderRow,
  type ReconciliationSummary,
  type Stage2Payout,
  type Stage2PayoutStatus,
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

const PAYOUT_STATUS_VARIANT: Record<Stage2PayoutStatus, "success" | "info" | "warning" | "outline"> = {
  banked: "success",
  posted: "info",
  attention: "warning",
  pending: "outline",
};

const PAYOUT_STATUS_LABEL: Record<Stage2PayoutStatus, string> = {
  banked: "Banked",
  posted: "Posted",
  attention: "Needs attention",
  pending: "Pending",
};

const AGING_VARIANT: Record<AgingBucket, "secondary" | "warning" | "destructive" | "outline"> = {
  none: "outline",
  "0_7d": "secondary",
  "7_30d": "warning",
  "30_plus": "destructive",
};

const SEVERITY_VARIANT: Record<ExceptionSeverity, "secondary" | "warning" | "destructive" | "outline"> = {
  LOW: "outline",
  MEDIUM: "secondary",
  HIGH: "warning",
  CRITICAL: "destructive",
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

// /finance/settlements/import only has Paymob + Bosta uploaders; a sub-gateway
// like paymob_accept settles within the Paymob CSV, so match on prefix and
// label the action with the importable parent. Anything else (Shopify Payments
// auto-payouts, manual receipts) is NOT CSV-importable — don't prescribe it.
const IMPORTABLE_SETTLEMENT_TARGETS: ReadonlyArray<{ prefix: string; label: string }> = [
  { prefix: "paymob", label: "Paymob" },
  { prefix: "bosta", label: "Bosta" },
];

function settlementImportTarget(dimensionValueCode: string): string | null {
  const code = (dimensionValueCode || "").toLowerCase();
  const match = IMPORTABLE_SETTLEMENT_TARGETS.find((t) => code.startsWith(t.prefix));
  return match ? match.label : null;
}

export default function ReconciliationPage() {
  const { toast } = useToast();

  const [summary, setSummary] = useState<ReconciliationSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [drilldownByProvider, setDrilldownByProvider] = useState<
    Record<number, ReconciliationDrilldown | null>
  >({});
  const [ordersByProvider, setOrdersByProvider] = useState<
    Record<number, ReconciliationOrders | null>
  >({});
  const [expandedProvider, setExpandedProvider] = useState<number | null>(null);
  const [drilldownLoading, setDrilldownLoading] = useState<number | null>(null);
  const [drilldownTabByProvider, setDrilldownTabByProvider] = useState<
    Record<number, "orders" | "lines">
  >({});

  const [pendingResolveByLine, setPendingResolveByLine] = useState<
    Record<number, { reason: DifferenceReason | ""; notes: string; submitting: boolean }>
  >({});

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
    if (!drilldownTabByProvider[row.provider_id]) {
      setDrilldownTabByProvider((prev) => ({ ...prev, [row.provider_id as number]: "orders" }));
    }
    // Fetch the orders view by default. JE-lines view fetches lazily on tab switch.
    if (!ordersByProvider[row.provider_id]) {
      setDrilldownLoading(row.provider_id);
      try {
        const { data } = await reconciliationService.orders(row.provider_id);
        setOrdersByProvider((prev) => ({
          ...prev,
          [row.provider_id as number]: data,
        }));
      } catch {
        toast({
          title: `Failed to load ${row.provider_name} orders.`,
          variant: "destructive",
        });
      } finally {
        setDrilldownLoading(null);
      }
    }
  };

  const handleSetTab = async (
    row: ReconciliationProviderRow,
    tab: "orders" | "lines"
  ) => {
    if (!row.provider_id) return;
    setDrilldownTabByProvider((prev) => ({ ...prev, [row.provider_id as number]: tab }));
    if (tab === "lines" && !drilldownByProvider[row.provider_id]) {
      setDrilldownLoading(row.provider_id);
      try {
        const { data } = await reconciliationService.drilldown(row.provider_id, row.account_id);
        setDrilldownByProvider((prev) => ({
          ...prev,
          [row.provider_id as number]: data,
        }));
      } catch {
        toast({
          title: `Failed to load ${row.provider_name} JE lines.`,
          variant: "destructive",
        });
      } finally {
        setDrilldownLoading(null);
      }
    }
    if (tab === "orders" && !ordersByProvider[row.provider_id]) {
      setDrilldownLoading(row.provider_id);
      try {
        const { data } = await reconciliationService.orders(row.provider_id);
        setOrdersByProvider((prev) => ({
          ...prev,
          [row.provider_id as number]: data,
        }));
      } catch {
        toast({
          title: `Failed to load ${row.provider_name} orders.`,
          variant: "destructive",
        });
      } finally {
        setDrilldownLoading(null);
      }
    }
  };

  const stage1Rows = useMemo(() => summary?.stage1.providers ?? [], [summary]);
  const totals = summary?.stage1.totals;
  const needsReview = summary?.needs_review;

  // "What to do next" — providers still owed money, most-aged first. Derived
  // entirely from the Stage-1 rows already on the wire (no extra request); the
  // exception queue can't surface this because no detector reads the clearing
  // balances these numbers come from.
  const attentionProviders = useMemo(
    () =>
      stage1Rows
        .filter((r) => Number(r.open_balance) > 0)
        .sort((a, b) => b.days_outstanding - a.days_outstanding),
    [stage1Rows]
  );

  const stage2Payouts = useMemo(() => summary?.stage2.payouts ?? [], [summary]);

  // Stage-2 prompt rows: Stage-1 providers still owed money that have NO
  // payout in the ledger — every empty state gets a next action (import the
  // CSV, or wait for the connector's payout sync). Same derivation family as
  // the "What to do next" panel: purely from data already on the wire.
  const stage2PromptRows = useMemo(() => {
    const payoutProviders = new Set(stage2Payouts.map((p) => p.provider.toLowerCase()));
    return stage1Rows.filter(
      (r) =>
        Number(r.open_balance) > 0 &&
        !payoutProviders.has((r.dimension_value_code || "").toLowerCase())
    );
  }, [stage1Rows, stage2Payouts]);

  const updateResolve = (
    lineId: number,
    patch: Partial<{ reason: DifferenceReason | ""; notes: string; submitting: boolean }>
  ) => {
    setPendingResolveByLine((prev) => ({
      ...prev,
      [lineId]: {
        reason: prev[lineId]?.reason ?? "",
        notes: prev[lineId]?.notes ?? "",
        submitting: prev[lineId]?.submitting ?? false,
        ...patch,
      },
    }));
  };

  const handleResolve = async (item: NeedsReviewItem) => {
    const draft = pendingResolveByLine[item.bank_line_id];
    if (!draft?.reason) {
      toast({
        title: "Pick a reason for the difference first.",
        variant: "destructive",
      });
      return;
    }
    updateResolve(item.bank_line_id, { submitting: true });
    try {
      await reconciliationService.resolveDifference(item.bank_line_id, {
        reason: draft.reason,
        notes: draft.notes || undefined,
      });
      toast({ title: `Difference resolved for batch ${item.batch_id || item.bank_line_id}.` });
      // Refresh the summary so the row leaves the queue and Stage 3 totals update.
      await fetchSummary(false);
      setPendingResolveByLine((prev) => {
        const next = { ...prev };
        delete next[item.bank_line_id];
        return next;
      });
    } catch (err) {
      const detail =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        "Could not post the adjustment JE.";
      toast({ title: detail, variant: "destructive" });
      updateResolve(item.bank_line_id, { submitting: false });
    }
  };

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
            {/* A16: 'Tell me the story' narrative banner */}
            {summary.narrative && (
              <Card className="border-primary/30 bg-primary/5">
                <CardContent className="py-4 text-sm leading-relaxed">
                  <p className="text-xs uppercase text-muted-foreground mb-1">
                    Tell me the story
                  </p>
                  {summary.narrative}
                </CardContent>
              </Card>
            )}

            {/* U1: Money Bridge — the "where is my money?" story as a picture */}
            {summary.money_flow && Number(summary.money_flow.total_sold) > 0 && (
              <MoneyBridge flow={summary.money_flow} />
            )}

            {/* A16: Needs Review queue — bank deposits matched within tolerance
               but with an unexplained difference. Operator picks a reason
               which posts the adjustment JE that drains the EBD residual. */}
            {needsReview && needsReview.items.length > 0 && (
              <NeedsReviewCard
                items={needsReview.items}
                pending={pendingResolveByLine}
                onChange={updateResolve}
                onResolve={handleResolve}
              />
            )}

            {/* "What to do next" — per-provider open balances ranked by age,
               each with a concrete next action. Pure client-side derivation
               from the Stage-1 rows; answers the merchant's "where do I look
               first?" without an extra request. */}
            {attentionProviders.length > 0 && (
              <Card className="border-amber-500/40 bg-amber-500/5">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <ClipboardCheck className="h-5 w-5 text-amber-600" />
                    What to do next
                    <Badge variant="warning">{attentionProviders.length}</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {attentionProviders.slice(0, 6).map((p) => (
                    <div
                      key={`${p.provider_id ?? p.dimension_value_code}-${p.account_id}`}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-background px-3 py-2 text-sm"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        {PROVIDER_ICON[p.provider_type]}
                        <span className="font-medium">{p.provider_name}</span>
                        <span className="text-muted-foreground">
                          {formatMoney(p.open_balance)} open
                        </span>
                        {p.oldest_entry_date && (
                          <span className="text-muted-foreground">
                            · oldest {p.oldest_entry_date}
                          </span>
                        )}
                        <Badge variant={AGING_VARIANT[p.aging_bucket]}>
                          {AGING_LABEL[p.aging_bucket]}
                        </Badge>
                      </div>
                      {(() => {
                        const target = settlementImportTarget(p.dimension_value_code);
                        if (target) {
                          return (
                            <Link
                              href="/finance/settlements/import"
                              className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
                            >
                              Import {target} settlement
                              <ChevronRight className="h-4 w-4" />
                            </Link>
                          );
                        }
                        // Not CSV-importable: don't send the operator to a page
                        // that can't act. Gateways/marketplaces settle via
                        // automated payout sync; manual/bank receipts reconcile
                        // against the bank statement.
                        const hint =
                          p.provider_type === "gateway" || p.provider_type === "marketplace"
                            ? "Awaiting payout sync"
                            : "Reconcile manually";
                        return <span className="text-xs text-muted-foreground">{hint}</span>;
                      })()}
                    </div>
                  ))}
                  {attentionProviders.length > 6 && (
                    <p className="text-xs text-muted-foreground">
                      +{attentionProviders.length - 6} more provider(s) with open balances — see Stage 1 below.
                    </p>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Exception queue — surface the (previously orphaned) detect →
               investigate → resolve lifecycle next to the numbers it explains.
               Links to the full /banking/exceptions queue. */}
            {summary.exceptions?.available &&
              summary.exceptions.total_open > 0 &&
              (() => {
                const critical = summary.exceptions.by_severity?.CRITICAL ?? 0;
                const high = summary.exceptions.by_severity?.HIGH ?? 0;
                const parts = [
                  `${summary.exceptions.total_open} open`,
                  critical > 0 ? `${critical} critical` : null,
                  high > 0 ? `${high} high` : null,
                ].filter(Boolean);
                return (
                  <Card
                    className={
                      critical > 0
                        ? "border-destructive/40 bg-destructive/5"
                        : "border-amber-500/40 bg-amber-500/5"
                    }
                  >
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2 text-base">
                        <AlertCircle className="h-5 w-5 text-destructive" />
                        Exceptions to investigate
                        <Badge variant={critical > 0 ? "destructive" : "warning"}>
                          {summary.exceptions.total_open}
                        </Badge>
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <p className="text-muted-foreground">{parts.join(" · ")}</p>
                        <Link
                          href="/banking/exceptions"
                          className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
                        >
                          Review queue
                          <ChevronRight className="h-4 w-4" />
                        </Link>
                      </div>
                      {(summary.exceptions.items?.length ?? 0) > 0 && (
                        <div className="space-y-1.5">
                          {summary.exceptions.items!.map((it) => (
                            <div
                              key={it.public_id}
                              className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-background px-3 py-2"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant={SEVERITY_VARIANT[it.severity] ?? "secondary"}>
                                  {it.severity}
                                </Badge>
                                <span className="font-medium">{it.title}</span>
                                {it.reference_label && (
                                  <span className="text-xs text-muted-foreground">
                                    {it.reference_label}
                                  </span>
                                )}
                              </div>
                              {it.amount && Number(it.amount) !== 0 && (
                                <span className="text-muted-foreground">
                                  {formatMoney(it.amount)} {it.currency}
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                );
              })()}

            {/* Top-line totals */}
            {totals && (
              <div
                className={`grid gap-4 sm:grid-cols-2 ${
                  Number(totals.total_refunded ?? "0") > 0
                    ? "lg:grid-cols-5"
                    : "lg:grid-cols-4"
                }`}
              >
                <SummaryTile
                  label="Total Expected"
                  value={formatMoney(totals.total_expected)}
                  caption="Gross sold into clearing (Shopify)"
                  href="#stage-1"
                />
                <SummaryTile
                  label="Total Settled"
                  value={formatMoney(totals.total_settled)}
                  caption="Drained via provider settlements"
                  href="#stage-2"
                />
                {Number(totals.total_refunded ?? "0") > 0 && (
                  <SummaryTile
                    label="Total Refunded"
                    value={formatMoney(totals.total_refunded)}
                    caption="Drained via customer refunds"
                    href="#stage-1"
                  />
                )}
                <SummaryTile
                  label="Open Balance"
                  value={formatMoney(totals.open_balance)}
                  caption={`Across ${totals.providers_with_open_balance} provider(s)`}
                  emphasize
                  href="#stage-1"
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
                  href="#stage-1"
                />
              </div>
            )}

            {/* Stage 1 — Sales → Clearing */}
            <Card id="stage-1" className="scroll-mt-20">
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
                          <th className="py-2 pr-3 text-right">Refunded</th>
                          <th className="py-2 pr-3 text-right">Banked</th>
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
                          const orders = row.provider_id
                            ? ordersByProvider[row.provider_id]
                            : null;
                          const tab = row.provider_id
                            ? drilldownTabByProvider[row.provider_id] ?? "orders"
                            : "orders";
                          return (
                            <ProviderRow
                              key={`${row.account_id}-${row.dimension_value_id}`}
                              row={row}
                              isExpanded={isExpanded}
                              isLoading={drilldownLoading === row.provider_id}
                              drilldown={drill ?? null}
                              orders={orders ?? null}
                              tab={tab}
                              onToggle={() => handleToggleProvider(row)}
                              onSetTab={(t) => handleSetTab(row, t)}
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
            <Card id="stage-2" className="scroll-mt-20">
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
                      <Link
                        href="/finance/settlements/import"
                        className="rounded-md p-1 -m-1 hover:bg-muted/60 transition-colors cursor-pointer"
                        title="Open Import Settlements"
                      >
                        <p className="text-xs uppercase text-muted-foreground">Settlements posted</p>
                        <p className="text-lg font-semibold underline-offset-2 hover:underline">
                          {summary.stage2.settled_count ?? 0}
                        </p>
                      </Link>
                      <Link
                        href="/finance/settlements/import"
                        className="rounded-md p-1 -m-1 hover:bg-muted/60 transition-colors cursor-pointer"
                        title="Open Import Settlements"
                      >
                        <p className="text-xs uppercase text-muted-foreground">Net to bank</p>
                        <p className="text-lg font-semibold underline-offset-2 hover:underline">
                          {formatMoney(summary.stage2.settled_total ?? "0")}
                        </p>
                        <p className="text-[10px] text-muted-foreground">After provider fees</p>
                      </Link>
                    </div>
                    {summary.stage2.pending_csv_import_note && (
                      <div className="flex items-start gap-2 rounded-md border border-yellow-500/30 bg-yellow-500/10 p-3 text-xs">
                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-yellow-500" />
                        <span>{summary.stage2.pending_csv_import_note}</span>
                      </div>
                    )}

                    {(stage2Payouts.length > 0 || stage2PromptRows.length > 0) && (
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead className="border-b text-left text-xs uppercase text-muted-foreground">
                            <tr>
                              <th className="py-2 pr-3">Provider</th>
                              <th className="py-2 pr-3">Payout / Batch</th>
                              <th className="py-2 pr-3">Date</th>
                              <th className="py-2 pr-3 text-right">Gross</th>
                              <th className="py-2 pr-3 text-right">Fees</th>
                              <th className="py-2 pr-3 text-right">Net</th>
                              <th className="py-2 pr-3">Status</th>
                              <th className="py-2">Entry</th>
                            </tr>
                          </thead>
                          <tbody>
                            {stage2Payouts.map((p) => (
                              <Stage2PayoutRow key={`${p.provider}-${p.batch_id}`} payout={p} />
                            ))}
                            {stage2PromptRows.map((r) => {
                              const importTarget = settlementImportTarget(r.dimension_value_code);
                              return (
                                <tr
                                  key={`prompt-${r.account_id}-${r.dimension_value_id}`}
                                  className="border-b text-muted-foreground"
                                >
                                  <td className="py-2 pr-3">{r.provider_name}</td>
                                  <td className="py-2 pr-3 italic" colSpan={5}>
                                    No settlements yet — {formatMoney(r.open_balance)} still in clearing
                                  </td>
                                  <td className="py-2 pr-3" colSpan={2}>
                                    {importTarget ? (
                                      <Link
                                        href="/finance/settlements/import"
                                        className="font-medium text-primary hover:underline"
                                      >
                                        Import {importTarget} settlement →
                                      </Link>
                                    ) : r.provider_type === "gateway" || r.provider_type === "marketplace" ? (
                                      <span>Awaiting payout sync</span>
                                    ) : (
                                      <span>Reconcile manually</span>
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
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
            <Card id="stage-3" className="scroll-mt-20">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Building2 className="h-5 w-5" />
                  Stage 3 — Bank Match
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm">
                {summary.stage3.available ? (
                  <div className="flex flex-wrap gap-6">
                    <Link
                      href="/accounting/bank-reconciliation"
                      className="rounded-md p-1 -m-1 hover:bg-muted/60 transition-colors cursor-pointer"
                      title="Open Bank Reconciliation"
                    >
                      <p className="text-xs uppercase text-muted-foreground">Total bank lines</p>
                      <p className="text-lg font-semibold underline-offset-2 hover:underline">
                        {summary.stage3.total_lines ?? 0}
                      </p>
                    </Link>
                    <Link
                      href="/accounting/bank-reconciliation"
                      className="rounded-md p-1 -m-1 hover:bg-muted/60 transition-colors cursor-pointer"
                      title="Open Bank Reconciliation"
                    >
                      <p className="text-xs uppercase text-muted-foreground">Matched</p>
                      <p className="text-lg font-semibold underline-offset-2 hover:underline">
                        {summary.stage3.matched_lines ?? 0}
                      </p>
                    </Link>
                    <Link
                      href="/accounting/bank-reconciliation"
                      className="rounded-md p-1 -m-1 hover:bg-muted/60 transition-colors cursor-pointer"
                      title="Open Bank Reconciliation"
                    >
                      <p className="text-xs uppercase text-muted-foreground">Unmatched</p>
                      <p className="text-lg font-semibold underline-offset-2 hover:underline">
                        {summary.stage3.unmatched_lines ?? 0}
                      </p>
                    </Link>
                    {(summary.stage3.matched_with_unresolved_difference ?? 0) > 0 && (
                      <a
                        href="#needs-review-queue"
                        onClick={(e) => {
                          e.preventDefault();
                          document
                            .getElementById("needs-review-queue")
                            ?.scrollIntoView({ behavior: "smooth", block: "start" });
                        }}
                        className="rounded-md p-1 -m-1 hover:bg-amber-500/10 transition-colors cursor-pointer"
                        title="Jump to the Needs Review queue"
                      >
                        <p className="text-xs uppercase text-muted-foreground">
                          Needs review
                        </p>
                        <p className="text-lg font-semibold text-amber-600 underline-offset-2 hover:underline">
                          {summary.stage3.matched_with_unresolved_difference}
                        </p>
                      </a>
                    )}
                  </div>
                ) : (
                  <p className="text-muted-foreground italic">No bank statement lines imported yet.</p>
                )}

                {/* U3: durable match summary from ReconciliationLink — surfaces
                   the match confidence the engine computes but never showed. */}
                {summary.matches && summary.matches.total > 0 && (
                  <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 border-t pt-3 text-xs text-muted-foreground">
                    <span>
                      Matches:{" "}
                      <span className="font-medium text-foreground">{summary.matches.confirmed}</span> confirmed
                      {summary.matches.needs_review > 0 && (
                        <span className="text-amber-600"> · {summary.matches.needs_review} need review</span>
                      )}
                    </span>
                    {summary.matches.avg_confidence && (
                      <span>
                        Avg confidence:{" "}
                        <span className="font-medium text-foreground">{summary.matches.avg_confidence}%</span>
                      </span>
                    )}
                    <span>
                      Auto <span className="font-medium text-foreground">{summary.matches.auto_matched}</span> · Manual{" "}
                      <span className="font-medium text-foreground">{summary.matches.manually_matched}</span>
                    </span>
                  </div>
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
  href,
}: {
  label: string;
  value: string;
  caption?: string;
  emphasize?: boolean;
  variant?: "default" | "destructive";
  href?: string;
}) {
  const card = (
    <Card
      className={
        (variant === "destructive"
          ? "border-destructive/40 bg-destructive/5"
          : emphasize
          ? "border-primary/40"
          : "") +
        (href ? " transition hover:border-primary/60 hover:shadow-md cursor-pointer" : "")
      }
    >
      <CardContent className="space-y-1 py-4">
        <p className="text-xs uppercase text-muted-foreground">{label}</p>
        <p className="text-2xl font-semibold">{value}</p>
        {caption && <p className="text-xs text-muted-foreground">{caption}</p>}
      </CardContent>
    </Card>
  );
  if (href) {
    return (
      <Link href={href} className="block">
        {card}
      </Link>
    );
  }
  return card;
}

const MONEY_FLOW_COLOR: Record<MoneyFlow["segments"][number]["key"], string> = {
  settled: "bg-emerald-500",
  refunded: "bg-amber-500",
  open: "bg-sky-500",
};

function MoneyBridge({ flow }: { flow: MoneyFlow }) {
  const sold = Number(flow.total_sold);
  if (!Number.isFinite(sold) || sold <= 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ScrollText className="h-5 w-5" />
          Money Bridge
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-baseline justify-between text-sm">
          <span className="text-muted-foreground">Sold into clearing</span>
          <span className="font-semibold">
            {formatMoney(flow.total_sold)} {flow.currency}
          </span>
        </div>

        {/* Segmented waterfall — widths proportional to Sold. */}
        <div className="flex h-6 w-full overflow-hidden rounded-md border bg-muted">
          {flow.segments.map((s) => {
            const pct = Math.max(0, (Number(s.amount) / sold) * 100);
            if (pct <= 0) return null;
            return (
              <div
                key={s.key}
                className={`${MONEY_FLOW_COLOR[s.key]} h-full`}
                style={{ width: `${pct}%` }}
                title={`${s.label}: ${formatMoney(s.amount)}`}
              />
            );
          })}
        </div>

        {/* Legend — every segment named, value grouped directly under its label
            (not edge-aligned, which detached the value from its name). */}
        <div className="grid gap-x-6 gap-y-3 sm:grid-cols-3">
          {flow.segments.map((s) => (
            <div key={s.key} className="space-y-1">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${MONEY_FLOW_COLOR[s.key]}`} />
                <span>{s.label}</span>
              </div>
              <div className="ps-4 text-sm font-semibold tabular-nums">{formatMoney(s.amount)}</div>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap gap-x-6 gap-y-1 border-t pt-2 text-xs text-muted-foreground">
          <span>
            Reached the bank:{" "}
            <span className="font-medium text-foreground">{formatMoney(flow.banked)}</span>
          </span>
          {Number(flow.aged_over_30d) > 0 && (
            <span className="text-destructive">
              Open &gt; 30 days:{" "}
              <span className="font-medium">{formatMoney(flow.aged_over_30d)}</span>
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

const ORDER_STATUS_VARIANT: Record<OrderReconciliationStatus, "secondary" | "warning" | "success"> = {
  expected: "warning",
  settled: "secondary",
  banked: "success",
};

const ORDER_STATUS_LABEL: Record<OrderReconciliationStatus, string> = {
  expected: "Expected",
  settled: "Settled",
  banked: "Banked",
};

function Stage2PayoutRow({ payout }: { payout: Stage2Payout }) {
  // Banked payouts link to the clearance entry (the bank-side truth);
  // otherwise the settlement drain entry.
  const entryId = payout.clearance_entry_id ?? payout.settlement_entry_id;
  const entryNumber = payout.clearance_entry_number || payout.settlement_entry_number;
  return (
    <tr className="border-b">
      <td className="py-2 pr-3 font-medium">{payout.provider_name}</td>
      <td className="py-2 pr-3 font-mono text-xs">{payout.batch_id}</td>
      <td className="py-2 pr-3 whitespace-nowrap">{payout.payout_date ?? "—"}</td>
      <td className="py-2 pr-3 text-right font-mono">
        {formatMoney(payout.gross_amount)} <span className="text-xs text-muted-foreground">{payout.currency}</span>
      </td>
      <td className="py-2 pr-3 text-right font-mono">{formatMoney(payout.fees)}</td>
      <td className="py-2 pr-3 text-right font-mono font-semibold">{formatMoney(payout.net_amount)}</td>
      <td className="py-2 pr-3">
        <Badge variant={PAYOUT_STATUS_VARIANT[payout.status]}>{PAYOUT_STATUS_LABEL[payout.status]}</Badge>
      </td>
      <td className="py-2 whitespace-nowrap">
        {entryId ? (
          <Link
            href={`/accounting/journal-entries/${entryId}`}
            className="text-primary hover:underline"
          >
            {entryNumber}
          </Link>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
    </tr>
  );
}

function ProviderRow({
  row,
  isExpanded,
  isLoading,
  drilldown,
  orders,
  tab,
  onToggle,
  onSetTab,
}: {
  row: ReconciliationProviderRow;
  isExpanded: boolean;
  isLoading: boolean;
  drilldown: ReconciliationDrilldown | null;
  orders: ReconciliationOrders | null;
  tab: "orders" | "lines";
  onToggle: () => void;
  onSetTab: (t: "orders" | "lines") => void;
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
        <td className="py-2 pr-3 text-right">{formatMoney(row.total_refunded ?? "0")}</td>
        <td className="py-2 pr-3 text-right">{formatMoney(row.banked ?? "0")}</td>
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
          <td colSpan={10} className="bg-muted/30 px-3 py-3">
            {/* Tab bar — Orders (merchant-friendly) / JE lines (auditor view) */}
            <div className="mb-3 flex items-center gap-2">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSetTab("orders");
                }}
                className={`rounded-md border px-3 py-1 text-xs ${
                  tab === "orders"
                    ? "border-primary bg-primary/10 font-semibold"
                    : "border-input text-muted-foreground hover:bg-muted/50"
                }`}
              >
                Orders
                {orders && (
                  <span className="ml-1 text-[10px] text-muted-foreground">
                    ({orders.totals.order_count})
                  </span>
                )}
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSetTab("lines");
                }}
                className={`rounded-md border px-3 py-1 text-xs ${
                  tab === "lines"
                    ? "border-primary bg-primary/10 font-semibold"
                    : "border-input text-muted-foreground hover:bg-muted/50"
                }`}
              >
                JE Lines
                {drilldown && (
                  <span className="ml-1 text-[10px] text-muted-foreground">
                    ({drilldown.lines.length})
                  </span>
                )}
              </button>
            </div>

            {isLoading ? (
              <div className="flex items-center justify-center py-4">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            ) : tab === "orders" ? (
              orders && orders.orders.length > 0 ? (
                <OrdersTable orders={orders} row={row} />
              ) : (
                <p className="py-2 text-xs text-muted-foreground italic">
                  No orders for this provider.
                </p>
              )
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

function OrdersTable({
  orders,
  row,
}: {
  orders: ReconciliationOrders;
  row: ReconciliationProviderRow;
}) {
  const { toast } = useToast();
  const [openOrder, setOpenOrder] = useState<string | null>(null);
  const [traceByOrder, setTraceByOrder] = useState<Record<string, MoneyTrace>>({});
  const [traceLoading, setTraceLoading] = useState<string | null>(null);

  const handleTrace = async (orderId: string) => {
    if (openOrder === orderId) {
      setOpenOrder(null);
      return;
    }
    setOpenOrder(orderId);
    if (!traceByOrder[orderId] && row.provider_id) {
      setTraceLoading(orderId);
      try {
        const { data } = await reconciliationService.trace(row.provider_id, orderId);
        setTraceByOrder((prev) => ({ ...prev, [orderId]: data }));
      } catch {
        toast({ title: "Failed to load the money trace.", variant: "destructive" });
        setOpenOrder(null);
      } finally {
        setTraceLoading(null);
      }
    }
  };

  return (
    <>
      <div className="mb-2 grid grid-cols-4 gap-3 text-[11px]">
        <StatusTile
          label="Expected"
          variant="warning"
          count={orders.totals.by_status.expected}
          amount={row.total_debit}
        />
        <StatusTile label="Settled" variant="secondary" amount={row.total_credit} />
        <StatusTile label="Refunded" variant="destructive" amount={row.total_refunded ?? "0"} />
        <StatusTile label="Banked" variant="success" amount={row.banked ?? "0"} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="border-b text-left uppercase text-muted-foreground">
            <tr>
              <th className="py-1 pr-3">Order #</th>
              <th className="py-1 pr-3">Date</th>
              <th className="py-1 pr-3 text-right">Shopify Paid</th>
              <th className="py-1 pr-3">Settlement Batch</th>
              <th className="py-1 pr-3 text-right">Settled</th>
              <th className="py-1 pr-3">Status</th>
              <th className="py-1"></th>
            </tr>
          </thead>
          <tbody>
            {orders.orders.map((o) => {
              const isOpen = openOrder === o.shopify_order_id;
              return (
                <Fragment key={`${o.shopify_order_id}-${o.order_number}`}>
                  <tr className="border-b last:border-0">
                    <td className="py-1 pr-3 font-mono">{o.order_number}</td>
                    <td className="py-1 pr-3">{o.order_date ?? "—"}</td>
                    <td className="py-1 pr-3 text-right">{formatMoney(o.shopify_paid)}</td>
                    <td className="py-1 pr-3 font-mono">{o.settled_batch_id ?? "—"}</td>
                    <td className="py-1 pr-3 text-right">
                      {o.settled_amount ? formatMoney(o.settled_amount) : "—"}
                    </td>
                    <td className="py-1 pr-3">
                      <Badge variant={ORDER_STATUS_VARIANT[o.status]}>
                        {ORDER_STATUS_LABEL[o.status]}
                      </Badge>
                    </td>
                    <td className="py-1 text-right">
                      <button
                        type="button"
                        onClick={() => handleTrace(o.shopify_order_id)}
                        className="text-primary underline-offset-2 hover:underline"
                      >
                        {isOpen ? "Hide" : "Trace"}
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={7} className="bg-background px-3 py-2">
                        {traceLoading === o.shopify_order_id ? (
                          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                        ) : (
                          <MoneyTraceView trace={traceByOrder[o.shopify_order_id] ?? null} />
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function MoneyTraceView({ trace }: { trace: MoneyTrace | null }) {
  if (!trace) {
    return <span className="text-xs italic text-muted-foreground">No trace available.</span>;
  }
  const s1 = trace.stage1_sale;
  const s2 = trace.stage2_settlement;
  const s3 = trace.stage3_bank;
  return (
    <div className="space-y-1.5 text-xs">
      <div className="flex items-center gap-2">
        <span className="font-medium">Money trace · order {trace.order_number}</span>
        <Badge variant={ORDER_STATUS_VARIANT[trace.status]}>{ORDER_STATUS_LABEL[trace.status]}</Badge>
      </div>
      <ol className="space-y-1">
        <li>
          <span className="text-muted-foreground">1 · Sale —</span>{" "}
          {s1 ? (
            <span>
              {s1.invoice_number} ({formatMoney(s1.amount)}) via {s1.provider}
              {s1.je_entry_number ? ` · ${s1.je_entry_number}` : ""}
            </span>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </li>
        <li>
          <span className="text-muted-foreground">2 · Settlement —</span>{" "}
          {s2 ? (
            <span>
              batch {s2.batch_id}
              {s2.settled_amount ? ` (${formatMoney(s2.settled_amount)})` : ""}
              {s2.je_entry_number ? ` · ${s2.je_entry_number}` : ""}
            </span>
          ) : (
            <span className="text-muted-foreground">not settled yet</span>
          )}
        </li>
        <li>
          <span className="text-muted-foreground">3 · Bank —</span>{" "}
          {s3 ? (
            <span>
              {s3.clearance_je_entry_number ?? "—"}
              {s3.match
                ? ` · ${s3.match.status}${s3.match.confidence ? ` (${s3.match.confidence}% confidence)` : ""}`
                : ""}
            </span>
          ) : (
            <span className="text-muted-foreground">not banked yet</span>
          )}
        </li>
      </ol>
    </div>
  );
}

function StatusTile({
  label,
  variant,
  count,
  amount,
}: {
  label: string;
  variant: "secondary" | "warning" | "success" | "destructive";
  count?: number;
  amount: string;
}) {
  return (
    <div className="rounded border bg-background p-2">
      <div className="flex items-center gap-1">
        <Badge variant={variant}>{label}</Badge>
        {count !== undefined && (
          <span className="text-muted-foreground">×{count}</span>
        )}
      </div>
      <div className="mt-1 font-mono">{formatMoney(amount)}</div>
    </div>
  );
}

function NeedsReviewCard({
  items,
  pending,
  onChange,
  onResolve,
}: {
  items: NeedsReviewItem[];
  pending: Record<number, { reason: DifferenceReason | ""; notes: string; submitting: boolean }>;
  onChange: (
    lineId: number,
    patch: Partial<{ reason: DifferenceReason | ""; notes: string; submitting: boolean }>
  ) => void;
  onResolve: (item: NeedsReviewItem) => void | Promise<void>;
}) {
  return (
    <Card
      id="needs-review-queue"
      className="border-amber-500/40 bg-amber-50/30 dark:bg-amber-500/5 scroll-mt-4"
    >
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ClipboardCheck className="h-5 w-5 text-amber-600" />
          Needs Review ({items.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-muted-foreground">
          Bank deposits matched within tolerance but not equal to the
          expected settlement amount. Pick a reason to post the adjustment
          journal entry that drains the residual.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="py-2 pr-3">Date</th>
                <th className="py-2 pr-3">Provider</th>
                <th className="py-2 pr-3">Batch</th>
                <th className="py-2 pr-3 text-right">Expected</th>
                <th className="py-2 pr-3 text-right">Received</th>
                <th className="py-2 pr-3 text-right">Difference</th>
                <th className="py-2 pr-3">Reason</th>
                <th className="py-2 pr-3">Notes</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const draft = pending[item.bank_line_id] ?? {
                  reason: "",
                  notes: "",
                  submitting: false,
                };
                const directionLabel =
                  item.difference_direction === "short_paid"
                    ? "Short paid"
                    : "Over paid";
                return (
                  <tr key={item.bank_line_id} className="border-b last:border-0 align-top">
                    <td className="py-2 pr-3 text-xs">
                      <div>{item.line_date}</div>
                      <div className="text-muted-foreground">
                        {item.age_days}d ago
                      </div>
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs">
                      {item.provider_code || "—"}
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs">
                      {item.batch_id || "—"}
                    </td>
                    <td className="py-2 pr-3 text-right">{formatMoney(item.expected)}</td>
                    <td className="py-2 pr-3 text-right">{formatMoney(item.received)}</td>
                    <td className="py-2 pr-3 text-right">
                      <div className="font-semibold">{formatMoney(item.difference)}</div>
                      <div className="text-[10px] uppercase text-amber-600">
                        {directionLabel}
                      </div>
                    </td>
                    <td className="py-2 pr-3">
                      <select
                        className="h-9 w-full min-w-[10rem] rounded-md border border-input bg-background px-2 text-xs"
                        value={draft.reason}
                        disabled={draft.submitting}
                        onChange={(e) =>
                          onChange(item.bank_line_id, {
                            reason: e.target.value as DifferenceReason | "",
                          })
                        }
                      >
                        <option value="">Pick a reason…</option>
                        {item.available_reasons.map((r) => (
                          <option key={r.value} value={r.value}>
                            {r.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="py-2 pr-3">
                      <input
                        type="text"
                        placeholder="Optional note"
                        maxLength={255}
                        className="h-9 w-full min-w-[10rem] rounded-md border border-input bg-background px-2 text-xs"
                        value={draft.notes}
                        disabled={draft.submitting}
                        onChange={(e) =>
                          onChange(item.bank_line_id, { notes: e.target.value })
                        }
                      />
                    </td>
                    <td className="py-2 text-right">
                      <Button
                        size="sm"
                        disabled={!draft.reason || draft.submitting}
                        onClick={() => onResolve(item)}
                      >
                        {draft.submitting ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          "Resolve"
                        )}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
