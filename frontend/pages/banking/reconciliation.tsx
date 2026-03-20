import { useState, useEffect, useCallback } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  ArrowLeftRight,
  Loader2,
  Zap,
  Search,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Link2,
  Unlink,
  ArrowDownLeft,
  ArrowUpRight,
  Eye,
  Ban,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  bankService,
  BankTransaction,
  ReconciliationOverview,
  PayoutSuggestion,
  PayoutExplanation,
  UnmatchedPayout,
} from "@/services/bank.service";

// =============================================================================
// Sub-Components
// =============================================================================

function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="text-center">
      <p className="text-xs font-medium text-muted-foreground mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color || ""}`}>{value}</p>
      {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
    </div>
  );
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  if (confidence >= 90)
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" /> {confidence}%
      </Badge>
    );
  if (confidence >= 75)
    return (
      <Badge variant="warning" className="gap-1">
        <AlertTriangle className="h-3 w-3" /> {confidence}%
      </Badge>
    );
  return (
    <Badge variant="secondary" className="gap-1">
      {confidence}%
    </Badge>
  );
}

// =============================================================================
// Payout Explainer Panel
// =============================================================================

function PayoutExplainerPanel({
  explanation,
  onClose,
}: {
  explanation: PayoutExplanation;
  onClose: () => void;
}) {
  const s = explanation.summary;
  return (
    <div className="border rounded-lg bg-muted/30 p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-sm flex items-center gap-2">
          <Eye className="h-4 w-4" />
          Payout Breakdown — {explanation.platform === "stripe" ? "Stripe" : "Shopify"}{" "}
          <span className="font-mono text-xs text-muted-foreground">
            {explanation.payout_external_id}
          </span>
        </h3>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </div>

      {/* Summary row */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 text-sm">
        <div>
          <p className="text-xs text-muted-foreground">Charges</p>
          <p className="font-mono font-medium text-green-600">
            {Number(s.charges).toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Refunds</p>
          <p className="font-mono font-medium text-red-600">
            -{Number(s.refunds).toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Fees</p>
          <p className="font-mono font-medium text-orange-600">
            -{Number(s.fees).toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Adjustments</p>
          <p className="font-mono font-medium">
            {Number(s.adjustments).toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Net (Bank Deposit)</p>
          <p className="font-mono font-bold">
            {Number(s.actual_net).toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </p>
        </div>
      </div>

      {s.has_discrepancy && (
        <div className="bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-md p-3">
          <p className="text-sm text-red-700 dark:text-red-400 font-medium flex items-center gap-2">
            <AlertTriangle className="h-4 w-4" />
            Discrepancy: {explanation.currency}{" "}
            {Number(s.discrepancy).toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </p>
          <p className="text-xs text-red-600 dark:text-red-500 mt-1">
            Computed net ({s.computed_net}) does not match actual net ({s.actual_net}).
          </p>
        </div>
      )}

      {/* Matched bank transaction */}
      {explanation.bank_transaction && (
        <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-md p-3">
          <p className="text-sm text-green-700 dark:text-green-400 font-medium flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4" />
            Matched to bank deposit
          </p>
          <p className="text-xs text-green-600 dark:text-green-500 mt-1">
            {explanation.bank_transaction.date} — {explanation.bank_transaction.description} —{" "}
            {explanation.bank_transaction.bank_account} —{" "}
            {Number(explanation.bank_transaction.amount).toLocaleString(undefined, {
              minimumFractionDigits: 2,
            })}
          </p>
        </div>
      )}

      {/* Transaction list */}
      {explanation.transactions.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b">
                <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Type</th>
                <th className="text-right px-2 py-1.5 font-medium text-muted-foreground">Amount</th>
                <th className="text-right px-2 py-1.5 font-medium text-muted-foreground">Fee</th>
                <th className="text-right px-2 py-1.5 font-medium text-muted-foreground">Net</th>
                <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Source</th>
                <th className="text-center px-2 py-1.5 font-medium text-muted-foreground">Verified</th>
              </tr>
            </thead>
            <tbody>
              {explanation.transactions.map((txn) => (
                <tr key={txn.id} className="border-b">
                  <td className="px-2 py-1.5">
                    <Badge variant="outline" className="text-xs">
                      {txn.type}
                    </Badge>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {Number(txn.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-orange-600">
                    {Number(txn.fee) !== 0
                      ? `-${Math.abs(Number(txn.fee)).toLocaleString(undefined, { minimumFractionDigits: 2 })}`
                      : "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono font-medium">
                    {Number(txn.net).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </td>
                  <td className="px-2 py-1.5 font-mono text-muted-foreground truncate max-w-[140px]">
                    {txn.source_id || "—"}
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    {txn.verified ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-green-500 inline" />
                    ) : (
                      <XCircle className="h-3.5 w-3.5 text-muted-foreground inline" />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {explanation.transactions.length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-4">
          No transaction details available for this payout.
        </p>
      )}
    </div>
  );
}

// =============================================================================
// Match Suggestions Row
// =============================================================================

function SuggestionsRow({
  tx,
  suggestions,
  loadingSuggestions,
  onMatch,
  onExplain,
}: {
  tx: BankTransaction;
  suggestions: PayoutSuggestion[];
  loadingSuggestions: boolean;
  onMatch: (suggestion: PayoutSuggestion) => void;
  onExplain: (platform: string, payoutId: number) => void;
}) {
  if (loadingSuggestions) {
    return (
      <tr>
        <td colSpan={7} className="px-3 py-4 text-center">
          <Loader2 className="h-4 w-4 animate-spin inline me-2" />
          Finding matching payouts...
        </td>
      </tr>
    );
  }

  if (suggestions.length === 0) {
    return (
      <tr>
        <td colSpan={7} className="px-3 py-4 text-center text-sm text-muted-foreground">
          No matching payouts found for this deposit.
        </td>
      </tr>
    );
  }

  return (
    <>
      {suggestions.map((s) => (
        <tr key={`${s.platform}-${s.id}`} className="bg-blue-50/50 dark:bg-blue-950/20">
          <td className="px-3 py-2 text-xs text-muted-foreground" />
          <td className="px-3 py-2 text-sm" colSpan={2}>
            <span className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs capitalize">
                {s.platform}
              </Badge>
              <span className="font-mono text-xs">{s.payout_id}</span>
              <span className="text-muted-foreground text-xs">({s.payout_date})</span>
            </span>
          </td>
          <td className="px-3 py-2 text-right font-mono text-sm">
            {Number(s.net_amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </td>
          <td className="px-3 py-2 text-center">
            <ConfidenceBadge confidence={s.confidence} />
          </td>
          <td />
          <td className="px-3 py-2 text-center">
            <div className="flex items-center justify-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onExplain(s.platform, s.id)}
                title="Explain payout"
              >
                <Eye className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="default"
                size="sm"
                onClick={() => onMatch(s)}
                title="Match this payout"
              >
                <Link2 className="h-3.5 w-3.5 me-1" />
                Match
              </Button>
            </div>
          </td>
        </tr>
      ))}
    </>
  );
}

// =============================================================================
// Main Page
// =============================================================================

export default function ReconciliationPage() {
  const { toast } = useToast();

  // State
  const [overview, setOverview] = useState<ReconciliationOverview | null>(null);
  const [unmatchedDeposits, setUnmatchedDeposits] = useState<BankTransaction[]>([]);
  const [unmatchedPayouts, setUnmatchedPayouts] = useState<UnmatchedPayout[]>([]);
  const [loading, setLoading] = useState(true);
  const [autoMatching, setAutoMatching] = useState(false);

  // Expanded row for suggestions
  const [expandedTxId, setExpandedTxId] = useState<number | null>(null);
  const [suggestions, setSuggestions] = useState<PayoutSuggestion[]>([]);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);

  // Payout explainer
  const [explanation, setExplanation] = useState<PayoutExplanation | null>(null);
  const [loadingExplanation, setLoadingExplanation] = useState(false);

  // Tab
  const [tab, setTab] = useState<"deposits" | "payouts">("deposits");

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [overviewRes, depositsRes, payoutsRes] = await Promise.all([
        bankService.getReconciliationOverview(),
        bankService.getTransactions({ status: "UNMATCHED", type: "CREDIT", limit: 200 }),
        bankService.getUnmatchedPayouts(),
      ]);
      setOverview(overviewRes.data);
      setUnmatchedDeposits(depositsRes.data.results);
      setUnmatchedPayouts(payoutsRes.data.payouts);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleAutoMatch() {
    setAutoMatching(true);
    try {
      const { data } = await bankService.autoMatch();
      toast({
        title: `Auto-matched ${data.matched} of ${data.total} transactions.`,
      });
      loadData();
      setExpandedTxId(null);
      setExplanation(null);
    } catch {
      toast({ title: "Auto-match failed.", variant: "destructive" });
    } finally {
      setAutoMatching(false);
    }
  }

  async function handleExpandSuggestions(txId: number) {
    if (expandedTxId === txId) {
      setExpandedTxId(null);
      return;
    }
    setExpandedTxId(txId);
    setSuggestions([]);
    setLoadingSuggestions(true);
    try {
      const { data } = await bankService.getMatchSuggestions(txId);
      setSuggestions(data.suggestions);
    } finally {
      setLoadingSuggestions(false);
    }
  }

  async function handleManualMatch(txId: number, suggestion: PayoutSuggestion) {
    try {
      await bankService.manualMatch(txId, suggestion.platform, suggestion.id);
      toast({ title: "Transaction matched to payout." });
      setExpandedTxId(null);
      setExplanation(null);
      loadData();
    } catch {
      toast({ title: "Match failed.", variant: "destructive" });
    }
  }

  async function handleExplain(platform: string, payoutId: number) {
    setLoadingExplanation(true);
    try {
      const { data } = await bankService.explainPayout(platform, payoutId);
      setExplanation(data);
    } catch {
      toast({ title: "Could not load payout details.", variant: "destructive" });
    } finally {
      setLoadingExplanation(false);
    }
  }

  async function handleExclude(txId: number) {
    try {
      await bankService.updateTransaction(txId, { action: "exclude" });
      toast({ title: "Transaction excluded." });
      loadData();
    } catch {
      toast({ title: "Action failed.", variant: "destructive" });
    }
  }

  if (loading) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center py-24">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      </AppLayout>
    );
  }

  const o = overview!;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Reconciliation"
          subtitle="Match bank deposits to platform payouts"
        />

        {/* Overview Cards */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card>
            <CardContent className="pt-6">
              <StatCard
                label="Match Rate"
                value={`${o.match_rate}%`}
                color={
                  o.match_rate >= 80
                    ? "text-green-600"
                    : o.match_rate >= 50
                    ? "text-yellow-600"
                    : "text-red-600"
                }
              />
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <StatCard
                label="Unmatched Deposits"
                value={o.bank.unmatched}
                sub={`${Number(o.bank.unmatched_deposits).toLocaleString(undefined, { minimumFractionDigits: 2 })} total`}
              />
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <StatCard
                label="Unmatched Payouts"
                value={o.payouts.unmatched}
                sub={`${Number(o.payouts.unmatched_amount).toLocaleString(undefined, { minimumFractionDigits: 2 })} total`}
              />
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <StatCard
                label="Platform Payouts"
                value={o.payouts.total}
                sub={`Stripe: ${o.payouts.stripe_count} · Shopify: ${o.payouts.shopify_count}`}
              />
            </CardContent>
          </Card>
        </div>

        {/* Auto-Match Button */}
        <div className="flex items-center gap-3">
          <Button onClick={handleAutoMatch} disabled={autoMatching}>
            {autoMatching ? (
              <Loader2 className="h-4 w-4 animate-spin me-2" />
            ) : (
              <Zap className="h-4 w-4 me-2" />
            )}
            Auto-Match All
          </Button>
          <p className="text-sm text-muted-foreground">
            Automatically match bank deposits to platform payouts by amount and date.
          </p>
        </div>

        {/* Payout Explainer (when open) */}
        {loadingExplanation && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        )}
        {explanation && !loadingExplanation && (
          <PayoutExplainerPanel
            explanation={explanation}
            onClose={() => setExplanation(null)}
          />
        )}

        {/* Tabs */}
        <div className="flex gap-1 border-b">
          <button
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === "deposits"
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setTab("deposits")}
          >
            Unmatched Bank Deposits ({unmatchedDeposits.length})
          </button>
          <button
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === "payouts"
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setTab("payouts")}
          >
            Unmatched Payouts ({unmatchedPayouts.length})
          </button>
        </div>

        {/* Unmatched Bank Deposits Tab */}
        {tab === "deposits" && (
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-base">
                <ArrowDownLeft className="h-5 w-5 text-green-600" />
                Unmatched Bank Deposits
              </CardTitle>
            </CardHeader>
            <CardContent>
              {unmatchedDeposits.length === 0 ? (
                <p className="text-sm text-muted-foreground py-8 text-center">
                  No unmatched deposits. All bank deposits are matched to payouts.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b">
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground w-8" />
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">
                          Date
                        </th>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">
                          Description
                        </th>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">
                          Account
                        </th>
                        <th className="text-right px-3 py-2 font-medium text-muted-foreground">
                          Amount
                        </th>
                        <th className="text-center px-3 py-2 font-medium text-muted-foreground">
                          Suggestions
                        </th>
                        <th className="text-center px-3 py-2 font-medium text-muted-foreground">
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {unmatchedDeposits.map((tx) => {
                        const isExpanded = expandedTxId === tx.id;
                        return (
                          <>
                            <tr
                              key={tx.id}
                              className={`border-b hover:bg-muted/50 cursor-pointer ${
                                isExpanded ? "bg-muted/30" : ""
                              }`}
                              onClick={() => handleExpandSuggestions(tx.id)}
                            >
                              <td className="px-3 py-2.5">
                                {isExpanded ? (
                                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                ) : (
                                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                                )}
                              </td>
                              <td className="px-3 py-2.5 whitespace-nowrap font-mono text-xs">
                                {tx.transaction_date}
                              </td>
                              <td className="px-3 py-2.5 max-w-[300px] truncate">
                                {tx.description}
                              </td>
                              <td className="px-3 py-2.5 text-xs text-muted-foreground">
                                {tx.bank_account_name}
                              </td>
                              <td className="px-3 py-2.5 text-right whitespace-nowrap font-mono font-medium text-green-600">
                                <ArrowDownLeft className="inline h-3 w-3 me-1" />
                                {Number(tx.amount).toLocaleString(undefined, {
                                  minimumFractionDigits: 2,
                                })}
                              </td>
                              <td className="px-3 py-2.5 text-center">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleExpandSuggestions(tx.id);
                                  }}
                                >
                                  <Search className="h-3.5 w-3.5 me-1" />
                                  Find Match
                                </Button>
                              </td>
                              <td className="px-3 py-2.5 text-center">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleExclude(tx.id);
                                  }}
                                  title="Exclude from reconciliation"
                                >
                                  <Ban className="h-3.5 w-3.5" />
                                </Button>
                              </td>
                            </tr>
                            {isExpanded && (
                              <SuggestionsRow
                                key={`suggestions-${tx.id}`}
                                tx={tx}
                                suggestions={suggestions}
                                loadingSuggestions={loadingSuggestions}
                                onMatch={(s) => handleManualMatch(tx.id, s)}
                                onExplain={handleExplain}
                              />
                            )}
                          </>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Unmatched Payouts Tab */}
        {tab === "payouts" && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ArrowUpRight className="h-5 w-5 text-blue-600" />
                Unmatched Platform Payouts
              </CardTitle>
            </CardHeader>
            <CardContent>
              {unmatchedPayouts.length === 0 ? (
                <p className="text-sm text-muted-foreground py-8 text-center">
                  All platform payouts are matched to bank deposits.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b">
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">
                          Platform
                        </th>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">
                          Payout ID
                        </th>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">
                          Date
                        </th>
                        <th className="text-right px-3 py-2 font-medium text-muted-foreground">
                          Gross
                        </th>
                        <th className="text-right px-3 py-2 font-medium text-muted-foreground">
                          Fees
                        </th>
                        <th className="text-right px-3 py-2 font-medium text-muted-foreground">
                          Net (Bank)
                        </th>
                        <th className="text-center px-3 py-2 font-medium text-muted-foreground">
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {unmatchedPayouts.map((p) => (
                        <tr key={`${p.platform}-${p.id}`} className="border-b hover:bg-muted/50">
                          <td className="px-3 py-2.5">
                            <Badge variant="outline" className="capitalize">
                              {p.platform}
                            </Badge>
                          </td>
                          <td className="px-3 py-2.5 font-mono text-xs">{p.payout_id}</td>
                          <td className="px-3 py-2.5 font-mono text-xs">{p.payout_date}</td>
                          <td className="px-3 py-2.5 text-right font-mono">
                            {Number(p.gross_amount).toLocaleString(undefined, {
                              minimumFractionDigits: 2,
                            })}
                          </td>
                          <td className="px-3 py-2.5 text-right font-mono text-orange-600">
                            {Number(p.fees).toLocaleString(undefined, {
                              minimumFractionDigits: 2,
                            })}
                          </td>
                          <td className="px-3 py-2.5 text-right font-mono font-medium">
                            {Number(p.net_amount).toLocaleString(undefined, {
                              minimumFractionDigits: 2,
                            })}
                          </td>
                          <td className="px-3 py-2.5 text-center">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleExplain(p.platform, p.id)}
                              title="View payout breakdown"
                            >
                              <Eye className="h-3.5 w-3.5 me-1" />
                              Explain
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
