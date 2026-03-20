import { useState, useEffect, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Clock,
  ArrowRight,
  Loader2,
  TrendingUp,
  Receipt,
  Banknote,
  Search,
  ChevronDown,
  ChevronUp,
  ExternalLink,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { PageHeader } from "@/components/common";
import {
  shopifyService,
  ReconciliationSummary,
  PayoutListItem,
  PayoutReconciliation,
  TransactionMatch,
} from "@/services/shopify.service";

// ── Helpers ──────────────────────────────────────────────────────

function fmt(amount: string | number, currency = "USD") {
  const n = typeof amount === "string" ? parseFloat(amount) : amount;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(n);
}

function getStatusConfig(status: string) {
  switch (status) {
    case "verified":
      return {
        label: "Matched",
        icon: CheckCircle2,
        color: "text-green-400",
        bg: "bg-green-500/20",
        badge: "success" as const,
      };
    case "partial":
      return {
        label: "Partial",
        icon: AlertTriangle,
        color: "text-yellow-400",
        bg: "bg-yellow-500/20",
        badge: "warning" as const,
      };
    case "discrepancy":
      return {
        label: "Mismatch",
        icon: XCircle,
        color: "text-red-400",
        bg: "bg-red-500/20",
        badge: "destructive" as const,
      };
    default:
      return {
        label: "Unverified",
        icon: Clock,
        color: "text-muted-foreground",
        bg: "bg-muted",
        badge: "secondary" as const,
      };
  }
}

function getDefaultDateRange() {
  const now = new Date();
  const firstDay = new Date(now.getFullYear(), now.getMonth(), 1);
  const lastDay = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  return {
    from: firstDay.toISOString().split("T")[0],
    to: lastDay.toISOString().split("T")[0],
  };
}

// ── Main Page ────────────────────────────────────────────────────

export default function ReconciliationPage() {
  const defaultRange = useMemo(getDefaultDateRange, []);

  const [dateFrom, setDateFrom] = useState(defaultRange.from);
  const [dateTo, setDateTo] = useState(defaultRange.to);
  const [summary, setSummary] = useState<ReconciliationSummary | null>(null);
  const [payouts, setPayouts] = useState<PayoutListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedPayout, setExpandedPayout] = useState<number | null>(null);
  const [payoutDetail, setPayoutDetail] = useState<PayoutReconciliation | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  async function loadData() {
    setLoading(true);
    try {
      const [summaryRes, payoutsRes] = await Promise.allSettled([
        shopifyService.getReconciliationSummary(dateFrom, dateTo),
        shopifyService.getPayouts(1),
      ]);

      if (summaryRes.status === "fulfilled") {
        setSummary(summaryRes.value.data);
      }
      if (payoutsRes.status === "fulfilled") {
        setPayouts(payoutsRes.value.data.results);
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, [dateFrom, dateTo]);

  async function togglePayoutDetail(payoutId: number) {
    if (expandedPayout === payoutId) {
      setExpandedPayout(null);
      setPayoutDetail(null);
      return;
    }

    setExpandedPayout(payoutId);
    setDetailLoading(true);
    try {
      const res = await shopifyService.getPayoutReconciliation(payoutId);
      setPayoutDetail(res.data);
    } catch {
      setPayoutDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }

  async function handleVerifyPayout(payoutId: number) {
    try {
      await shopifyService.verifyPayout(payoutId);
      // Reload data after verification
      await loadData();
      if (expandedPayout === payoutId) {
        const res = await shopifyService.getPayoutReconciliation(payoutId);
        setPayoutDetail(res.data);
      }
    } catch {
      // Error handled by api client
    }
  }

  const matchRate = summary ? parseFloat(summary.match_rate) : 0;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Payout Verification"
          subtitle="Verify Shopify payout transactions against local orders and fees"
        />

        {/* Date Range */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <label className="text-sm font-medium">From</label>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="rounded-md border bg-background px-3 py-1.5 text-sm"
                />
              </div>
              <div className="flex items-center gap-2">
                <label className="text-sm font-medium">To</label>
                <input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="rounded-md border bg-background px-3 py-1.5 text-sm"
                />
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={loadData}
                disabled={loading}
              >
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Search className="h-4 w-4" />
                )}
                <span className="ms-2">Refresh</span>
              </Button>
            </div>
          </CardContent>
        </Card>

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-16">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : !summary || summary.total_payouts === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-16 text-center">
              <Receipt className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">No Payouts Found</h3>
              <p className="text-sm text-muted-foreground max-w-md">
                No Shopify payouts found for this period. Sync payouts from the
                Shopify settings page, or adjust the date range.
              </p>
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Summary Stats */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* Match Rate */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Match Rate</CardTitle>
                  <TrendingUp className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{matchRate}%</div>
                  <Progress
                    value={matchRate}
                    className={`mt-2 ${
                      matchRate >= 95
                        ? "[&>div]:bg-green-500"
                        : matchRate >= 70
                        ? "[&>div]:bg-yellow-500"
                        : "[&>div]:bg-red-500"
                    }`}
                  />
                  <p className="mt-1 text-xs text-muted-foreground">
                    {summary.matched_transactions}/{summary.total_transactions} transactions
                  </p>
                </CardContent>
              </Card>

              {/* Payouts by Status */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Payouts</CardTitle>
                  <Receipt className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{summary.total_payouts}</div>
                  <div className="mt-2 flex gap-2">
                    {summary.verified_payouts > 0 && (
                      <Badge variant="success">{summary.verified_payouts} matched</Badge>
                    )}
                    {summary.discrepancy_payouts > 0 && (
                      <Badge variant="destructive">{summary.discrepancy_payouts} mismatch</Badge>
                    )}
                    {summary.unverified_payouts > 0 && (
                      <Badge variant="secondary">{summary.unverified_payouts} pending</Badge>
                    )}
                  </div>
                </CardContent>
              </Card>

              {/* Net Deposited */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Net Deposited</CardTitle>
                  <Banknote className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{fmt(summary.total_net)}</div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Gross {fmt(summary.total_gross)} &minus; Fees {fmt(summary.total_fees)}
                  </p>
                </CardContent>
              </Card>

              {/* Unsettled Orders */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Unsettled Orders</CardTitle>
                  <Clock className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{fmt(summary.unmatched_order_total)}</div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Awaiting payout settlement
                  </p>
                </CardContent>
              </Card>
            </div>

            {/* Payouts List */}
            <Card>
              <CardHeader>
                <CardTitle>Payouts</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="divide-y">
                  {payouts.map((payout) => {
                    const cfg = getStatusConfig(payout.reconciliation_status);
                    const StatusIcon = cfg.icon;
                    const isExpanded = expandedPayout === payout.shopify_payout_id;

                    return (
                      <div key={payout.shopify_payout_id}>
                        {/* Payout Row */}
                        <button
                          onClick={() => togglePayoutDetail(payout.shopify_payout_id)}
                          className="flex w-full items-center justify-between px-6 py-4 text-start hover:bg-muted/50 transition-colors"
                        >
                          <div className="flex items-center gap-4 min-w-0">
                            <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${cfg.bg}`}>
                              <StatusIcon className={`h-5 w-5 ${cfg.color}`} />
                            </div>
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <p className="text-sm font-semibold font-mono">
                                  Payout #{payout.shopify_payout_id}
                                </p>
                                <Badge variant={cfg.badge}>{cfg.label}</Badge>
                              </div>
                              <p className="text-xs text-muted-foreground mt-0.5">
                                {new Date(payout.payout_date).toLocaleDateString(undefined, {
                                  year: "numeric",
                                  month: "short",
                                  day: "numeric",
                                })}
                                {" · "}
                                {payout.store_domain}
                                {payout.transactions_total > 0 && (
                                  <>
                                    {" · "}
                                    {payout.transactions_verified}/{payout.transactions_total} txns verified
                                  </>
                                )}
                              </p>
                            </div>
                          </div>

                          <div className="flex items-center gap-4 shrink-0">
                            <div className="text-end">
                              <p className="text-sm font-bold font-mono">
                                {fmt(payout.net_amount, payout.currency)}
                              </p>
                              <p className="text-xs text-muted-foreground">
                                Fees: {fmt(payout.fees, payout.currency)}
                              </p>
                            </div>
                            {isExpanded ? (
                              <ChevronUp className="h-4 w-4 text-muted-foreground" />
                            ) : (
                              <ChevronDown className="h-4 w-4 text-muted-foreground" />
                            )}
                          </div>
                        </button>

                        {/* Expanded Detail */}
                        {isExpanded && (
                          <PayoutDetailPanel
                            payoutId={payout.shopify_payout_id}
                            detail={payoutDetail}
                            loading={detailLoading}
                            currency={payout.currency}
                            onVerify={() => handleVerifyPayout(payout.shopify_payout_id)}
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </AppLayout>
  );
}

// ── Payout Detail Panel ──────────────────────────────────────────

function PayoutDetailPanel({
  payoutId,
  detail,
  loading,
  currency,
  onVerify,
}: {
  payoutId: number;
  detail: PayoutReconciliation | null;
  loading: boolean;
  currency: string;
  onVerify: () => void;
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 bg-muted/30">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="px-6 py-6 bg-muted/30">
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            No transaction data available. Verify this payout to fetch transactions from Shopify.
          </p>
          <Button size="sm" onClick={onVerify}>
            Verify Payout
          </Button>
        </div>
      </div>
    );
  }

  const cfg = getStatusConfig(detail.status);

  return (
    <div className="bg-muted/30 border-t">
      {/* Breakdown Summary */}
      <div className="px-6 py-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-lg border bg-background p-4">
            <p className="text-xs font-medium text-muted-foreground mb-1">Orders Total (Gross)</p>
            <p className="text-lg font-bold font-mono">{fmt(detail.gross_amount, currency)}</p>
          </div>
          <div className="rounded-lg border bg-background p-4">
            <p className="text-xs font-medium text-muted-foreground mb-1">Processing Fees</p>
            <p className="text-lg font-bold font-mono text-red-400">
              &minus;{fmt(detail.fees, currency)}
            </p>
          </div>
          <div className="rounded-lg border bg-background p-4">
            <p className="text-xs font-medium text-muted-foreground mb-1">Bank Deposit (Net)</p>
            <p className="text-lg font-bold font-mono text-green-400">
              {fmt(detail.net_amount, currency)}
            </p>
          </div>
        </div>

        {/* Variances */}
        {detail.discrepancies.length > 0 && (
          <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/5 p-3">
            <p className="text-sm font-semibold text-red-400 mb-1">Discrepancies Found</p>
            {detail.discrepancies.map((d, i) => (
              <p key={i} className="text-xs text-red-400/80">{d}</p>
            ))}
          </div>
        )}
      </div>

      {/* Transactions Table */}
      {detail.transactions.length > 0 && (
        <div className="px-6 pb-4">
          <p className="text-xs font-semibold text-muted-foreground mb-2 uppercase tracking-wide">
            Transactions ({detail.matched_transactions}/{detail.total_transactions} matched)
          </p>
          <div className="rounded-lg border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-3 py-2 text-start text-xs font-medium text-muted-foreground">Type</th>
                  <th className="px-3 py-2 text-start text-xs font-medium text-muted-foreground">Matched To</th>
                  <th className="px-3 py-2 text-end text-xs font-medium text-muted-foreground">Amount</th>
                  <th className="px-3 py-2 text-end text-xs font-medium text-muted-foreground">Fee</th>
                  <th className="px-3 py-2 text-end text-xs font-medium text-muted-foreground">Net</th>
                  <th className="px-3 py-2 text-center text-xs font-medium text-muted-foreground">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {detail.transactions.map((txn) => (
                  <TransactionRow key={txn.shopify_transaction_id} txn={txn} currency={currency} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Transaction Row ──────────────────────────────────────────────

function TransactionRow({ txn, currency }: { txn: TransactionMatch; currency: string }) {
  const typeColors: Record<string, string> = {
    charge: "text-green-400",
    refund: "text-red-400",
    adjustment: "text-yellow-400",
    payout: "text-blue-400",
  };

  const variance = parseFloat(txn.variance);

  return (
    <tr className="hover:bg-muted/30 transition-colors">
      <td className="px-3 py-2">
        <span className={`font-medium capitalize ${typeColors[txn.transaction_type] || "text-muted-foreground"}`}>
          {txn.transaction_type}
        </span>
      </td>
      <td className="px-3 py-2 text-muted-foreground">
        {txn.matched ? (
          <span>{txn.matched_to}</span>
        ) : (
          <span className="italic text-red-400/60">Unmatched</span>
        )}
        {variance !== 0 && (
          <span className="ms-2 text-xs text-yellow-400">
            (variance: {fmt(variance, currency)})
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-end font-mono">
        {fmt(txn.amount, currency)}
      </td>
      <td className="px-3 py-2 text-end font-mono text-muted-foreground">
        {parseFloat(txn.fee) !== 0 ? fmt(txn.fee, currency) : "—"}
      </td>
      <td className="px-3 py-2 text-end font-mono">
        {fmt(txn.net, currency)}
      </td>
      <td className="px-3 py-2 text-center">
        {txn.matched ? (
          <CheckCircle2 className="h-4 w-4 text-green-400 inline" />
        ) : (
          <XCircle className="h-4 w-4 text-red-400 inline" />
        )}
      </td>
    </tr>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
