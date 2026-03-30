import { useState, useEffect, useMemo } from "react";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Clock,
  Loader2,
  TrendingUp,
  Receipt,
  Banknote,
  Search,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { PageHeader } from "@/components/common";
import {
  stripeService,
  StripeReconciliationSummary,
  StripePayoutListItem,
  StripePayoutReconciliation,
  StripeTransactionMatch,
} from "@/services/stripe.service";

// ── Helpers ──────────────────────────────────────────────────────

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

export default function StripeReconciliationPage() {
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();
  const defaultRange = useMemo(getDefaultDateRange, []);

  const [dateFrom, setDateFrom] = useState(defaultRange.from);
  const [dateTo, setDateTo] = useState(defaultRange.to);
  const [summary, setSummary] = useState<StripeReconciliationSummary | null>(null);
  const [payouts, setPayouts] = useState<StripePayoutListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedPayout, setExpandedPayout] = useState<string | null>(null);
  const [payoutDetail, setPayoutDetail] = useState<StripePayoutReconciliation | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  async function loadData() {
    setLoading(true);
    try {
      const [summaryRes, payoutsRes] = await Promise.allSettled([
        stripeService.getReconciliationSummary(dateFrom, dateTo),
        stripeService.getPayouts(1),
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

  async function handleVerifyPayout(payoutId: string) {
    try {
      await stripeService.verifyPayout(payoutId);
      await loadData();
      if (expandedPayout === payoutId) {
        const res = await stripeService.getPayoutReconciliation(payoutId);
        setPayoutDetail(res.data);
      }
    } catch {
      // Error handled by api client
    }
  }

  async function togglePayoutDetail(payoutId: string) {
    if (expandedPayout === payoutId) {
      setExpandedPayout(null);
      setPayoutDetail(null);
      return;
    }

    setExpandedPayout(payoutId);
    setDetailLoading(true);
    try {
      const res = await stripeService.getPayoutReconciliation(payoutId);
      setPayoutDetail(res.data);
    } catch {
      setPayoutDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }

  const matchRate = summary ? parseFloat(summary.match_rate) : 0;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Payout Verification"
          subtitle="Verify Stripe payout transactions against local charges and fees"
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
              <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
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
                No Stripe payouts found for this period. Connect your Stripe
                account or adjust the date range.
              </p>
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Summary Stats */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
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

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Net Deposited</CardTitle>
                  <Banknote className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{formatCurrency(summary.total_net)}</div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Gross {formatCurrency(summary.total_gross)} &minus; Fees {formatCurrency(summary.total_fees)}
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Processing Fees</CardTitle>
                  <Clock className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-red-400">{formatCurrency(summary.total_fees)}</div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Stripe processing fees
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
                    const isExpanded = expandedPayout === payout.stripe_payout_id;

                    return (
                      <div key={payout.stripe_payout_id}>
                        <button
                          onClick={() => togglePayoutDetail(payout.stripe_payout_id)}
                          className="flex w-full items-center justify-between px-6 py-4 text-start hover:bg-muted/50 transition-colors"
                        >
                          <div className="flex items-center gap-4 min-w-0">
                            <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${cfg.bg}`}>
                              <StatusIcon className={`h-5 w-5 ${cfg.color}`} />
                            </div>
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <p className="text-sm font-semibold font-mono">
                                  {payout.stripe_payout_id}
                                </p>
                                <Badge variant={cfg.badge}>{cfg.label}</Badge>
                              </div>
                              <p className="text-xs text-muted-foreground mt-0.5">
                                {formatDate(payout.payout_date)}
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
                                {formatCurrency(payout.net_amount, payout.currency)}
                              </p>
                              <p className="text-xs text-muted-foreground">
                                Fees: {formatCurrency(payout.fees, payout.currency)}
                              </p>
                            </div>
                            {isExpanded ? (
                              <ChevronUp className="h-4 w-4 text-muted-foreground" />
                            ) : (
                              <ChevronDown className="h-4 w-4 text-muted-foreground" />
                            )}
                          </div>
                        </button>

                        {isExpanded && (
                          <PayoutDetailPanel
                            detail={payoutDetail}
                            loading={detailLoading}
                            currency={payout.currency}
                            onVerify={() => handleVerifyPayout(payout.stripe_payout_id)}
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
  detail,
  loading,
  currency,
  onVerify,
}: {
  detail: StripePayoutReconciliation | null;
  loading: boolean;
  currency: string;
  onVerify: () => void;
}) {
  const { formatCurrency, formatDate } = useCompanyFormat();
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
            No transaction data available. Run reconciliation to match transactions.
          </p>
          <Button size="sm" onClick={onVerify}>
            Reconcile Payout
          </Button>
        </div>
      </div>
    );
  }

  const hasUnmatched = detail.unmatched_transactions > 0;

  return (
    <div className="bg-muted/30 border-t">
      <div className="px-6 py-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-lg border bg-background p-4">
            <p className="text-xs font-medium text-muted-foreground mb-1">Charges Total (Gross)</p>
            <p className="text-lg font-bold font-mono">{formatCurrency(detail.gross_amount, currency)}</p>
          </div>
          <div className="rounded-lg border bg-background p-4">
            <p className="text-xs font-medium text-muted-foreground mb-1">Processing Fees</p>
            <p className="text-lg font-bold font-mono text-red-400">
              &minus;{formatCurrency(detail.fees, currency)}
            </p>
          </div>
          <div className="rounded-lg border bg-background p-4">
            <p className="text-xs font-medium text-muted-foreground mb-1">Bank Deposit (Net)</p>
            <p className="text-lg font-bold font-mono text-green-400">
              {formatCurrency(detail.net_amount, currency)}
            </p>
          </div>
        </div>

        {detail.discrepancies.length > 0 && (
          <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/5 p-3">
            <p className="text-sm font-semibold text-red-400 mb-1">Discrepancies Found</p>
            {detail.discrepancies.map((d, i) => (
              <p key={i} className="text-xs text-red-400/80">{d}</p>
            ))}
          </div>
        )}

        {hasUnmatched && (
          <div className="mt-4 flex items-center justify-between rounded-lg border border-yellow-500/30 bg-yellow-500/5 p-3">
            <p className="text-sm text-yellow-400">
              {detail.unmatched_transactions} unmatched transaction{detail.unmatched_transactions > 1 ? "s" : ""}
            </p>
            <Button size="sm" variant="outline" onClick={onVerify}>
              Reconcile Now
            </Button>
          </div>
        )}
      </div>

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
                  <TransactionRow key={txn.stripe_balance_txn_id} txn={txn} currency={currency} />
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

function TransactionRow({ txn, currency }: { txn: StripeTransactionMatch; currency: string }) {
  const { formatCurrency, formatDate } = useCompanyFormat();
  const typeColors: Record<string, string> = {
    charge: "text-green-400",
    refund: "text-red-400",
    adjustment: "text-yellow-400",
    payout: "text-blue-400",
  };

  return (
    <tr className="hover:bg-muted/30 transition-colors">
      <td className="px-3 py-2">
        <span className={`font-medium capitalize ${typeColors[txn.transaction_type] || "text-muted-foreground"}`}>
          {txn.transaction_type}
        </span>
      </td>
      <td className="px-3 py-2 text-muted-foreground">
        {txn.matched ? (
          <span className="font-mono text-xs">{txn.matched_to}</span>
        ) : (
          <span className="italic text-red-400/60">Unmatched</span>
        )}
      </td>
      <td className="px-3 py-2 text-end font-mono">{formatCurrency(txn.amount, currency)}</td>
      <td className="px-3 py-2 text-end font-mono text-muted-foreground">
        {parseFloat(txn.fee) !== 0 ? formatCurrency(txn.fee, currency) : "\u2014"}
      </td>
      <td className="px-3 py-2 text-end font-mono">{formatCurrency(txn.net, currency)}</td>
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
