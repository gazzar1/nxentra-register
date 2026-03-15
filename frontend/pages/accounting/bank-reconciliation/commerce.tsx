import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Loader2,
  Search,
  ShoppingBag,
  Banknote,
  Building2,
  AlertTriangle,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  bankReconciliationService,
  CommerceReconciliationData,
  PayoutGroup,
} from "@/services/bank-reconciliation.service";

function formatMoney(value: string | number, currency = "USD") {
  const n = typeof value === "string" ? Number(value) : value;
  return `${currency} ${n.toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
}

export default function CommerceReconciliationPage() {
  const router = useRouter();
  const { toast } = useToast();

  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<CommerceReconciliationData | null>(null);
  const [expandedPayout, setExpandedPayout] = useState<number | null>(null);

  const handleSearch = async () => {
    if (!periodStart || !periodEnd) {
      toast({ title: "Please select both start and end dates.", variant: "destructive" });
      return;
    }
    setLoading(true);
    try {
      const { data: result } = await bankReconciliationService.getCommerceReconciliation(
        periodStart,
        periodEnd,
      );
      setData(result);
    } catch {
      toast({ title: "Failed to load reconciliation data.", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  const togglePayout = (payoutId: number) => {
    setExpandedPayout(expandedPayout === payoutId ? null : payoutId);
  };

  const summary = data?.summary;
  const diff = summary ? Number(summary.commerce_vs_payout_diff) : 0;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Commerce Reconciliation"
          subtitle="Three-column view: Orders / Payouts / Bank Deposits"
          actions={
            <Button
              variant="outline"
              onClick={() => router.push("/accounting/bank-reconciliation")}
            >
              <ArrowLeft className="me-2 h-4 w-4" />
              Back
            </Button>
          }
        />

        {/* Date Range Selector */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex gap-4 items-end">
              <div className="space-y-1.5">
                <Label>Period Start</Label>
                <Input
                  type="date"
                  value={periodStart}
                  onChange={(e) => setPeriodStart(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Period End</Label>
                <Input
                  type="date"
                  value={periodEnd}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                />
              </div>
              <Button onClick={handleSearch} disabled={loading}>
                {loading ? (
                  <Loader2 className="me-2 h-4 w-4 animate-spin" />
                ) : (
                  <Search className="me-2 h-4 w-4" />
                )}
                Load
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Summary Cards */}
        {summary && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <Card>
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                  <ShoppingBag className="h-4 w-4" />
                  Orders
                </div>
                <p className="font-mono font-medium text-lg">
                  {formatMoney(summary.total_orders)}
                </p>
                <p className="text-xs text-muted-foreground">{summary.order_count} orders</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                  <ShoppingBag className="h-4 w-4" />
                  Refunds
                </div>
                <p className="font-mono font-medium text-lg text-red-600">
                  -{formatMoney(summary.total_refunds)}
                </p>
                <p className="text-xs text-muted-foreground">{summary.refund_count} refunds</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                  <Banknote className="h-4 w-4" />
                  Payouts (Gross)
                </div>
                <p className="font-mono font-medium text-lg">
                  {formatMoney(summary.total_gross_payouts)}
                </p>
                <p className="text-xs text-muted-foreground">
                  Fees: {formatMoney(summary.total_fees)}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                  <Building2 className="h-4 w-4" />
                  Bank Deposits
                </div>
                <p className="font-mono font-medium text-lg">
                  {formatMoney(summary.total_net_payouts)}
                </p>
                <p className="text-xs text-muted-foreground">
                  {summary.bank_matched_count}/{summary.payout_count} matched
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                  {diff === 0 ? (
                    <CheckCircle2 className="h-4 w-4 text-green-600" />
                  ) : (
                    <AlertTriangle className="h-4 w-4 text-red-600" />
                  )}
                  Difference
                </div>
                <p
                  className={`font-mono font-bold text-lg ${
                    diff === 0 ? "text-green-600" : "text-red-600"
                  }`}
                >
                  {formatMoney(summary.commerce_vs_payout_diff)}
                </p>
                <p className="text-xs text-muted-foreground">Orders - Refunds - Gross Payouts</p>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Payout Groups - Three Column Layout */}
        {data && data.payout_groups.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Settlement Periods</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {data.payout_groups.map((group) => (
                <PayoutGroupRow
                  key={group.payout.shopify_payout_id}
                  group={group}
                  expanded={expandedPayout === group.payout.shopify_payout_id}
                  onToggle={() => togglePayout(group.payout.shopify_payout_id)}
                />
              ))}
            </CardContent>
          </Card>
        )}

        {data && data.payout_groups.length === 0 && (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-12 gap-3">
              <Banknote className="h-10 w-10 text-muted-foreground" />
              <p className="text-muted-foreground">
                No payouts found in the selected period.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </AppLayout>
  );
}

function PayoutGroupRow({
  group,
  expanded,
  onToggle,
}: {
  group: PayoutGroup;
  expanded: boolean;
  onToggle: () => void;
}) {
  const isMatched = group.reconciliation_status === "matched";
  const p = group.payout;

  return (
    <div className="border rounded-lg overflow-hidden">
      {/* Summary Row */}
      <button
        onClick={onToggle}
        className="w-full text-left px-4 py-3 hover:bg-muted/50 transition-colors"
      >
        <div className="grid grid-cols-3 gap-4">
          {/* Column 1: Commerce */}
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase mb-1">
              Commerce
            </p>
            <p className="text-sm">
              {group.orders.length} orders, {group.refunds.length} refunds
            </p>
          </div>

          {/* Column 2: Payout */}
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase mb-1">
              Payout — {p.payout_date}
            </p>
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm">
                {formatMoney(p.net_amount, p.currency)}
              </span>
              <span className="text-xs text-muted-foreground">
                (gross {formatMoney(p.gross_amount, p.currency)} - fees{" "}
                {formatMoney(p.fees, p.currency)})
              </span>
            </div>
          </div>

          {/* Column 3: Bank */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase mb-1">
                Bank Deposit
              </p>
              {group.bank_deposit ? (
                <span className="font-mono text-sm">
                  {formatMoney(group.bank_deposit.amount, p.currency)}
                  <span className="text-xs text-muted-foreground ms-2">
                    {group.bank_deposit.line_date}
                  </span>
                </span>
              ) : (
                <span className="text-sm text-muted-foreground">Not matched</span>
              )}
            </div>
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                isMatched
                  ? "bg-green-100 text-green-700"
                  : "bg-red-100 text-red-700"
              }`}
            >
              {isMatched ? (
                <CheckCircle2 className="h-3 w-3" />
              ) : (
                <XCircle className="h-3 w-3" />
              )}
              {isMatched ? "Matched" : "Unmatched"}
            </span>
          </div>
        </div>
      </button>

      {/* Expanded Detail */}
      {expanded && (
        <div className="border-t bg-muted/30 px-4 py-3">
          <div className="grid grid-cols-3 gap-4">
            {/* Column 1: Orders Detail */}
            <div>
              <p className="text-xs font-medium uppercase text-muted-foreground mb-2">
                Orders
              </p>
              {group.orders.length === 0 ? (
                <p className="text-xs text-muted-foreground">No orders</p>
              ) : (
                <div className="space-y-1 max-h-[200px] overflow-y-auto">
                  {group.orders.map((o) => (
                    <div
                      key={o.id}
                      className="flex justify-between text-xs border-b pb-1"
                    >
                      <span>
                        {o.order_name}{" "}
                        <span className="text-muted-foreground">{o.order_date}</span>
                      </span>
                      <span className="font-mono text-green-700">
                        +{Number(o.total_price).toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {group.refunds.length > 0 && (
                <>
                  <p className="text-xs font-medium uppercase text-muted-foreground mt-3 mb-2">
                    Refunds
                  </p>
                  <div className="space-y-1 max-h-[200px] overflow-y-auto">
                    {group.refunds.map((r) => (
                      <div
                        key={r.id}
                        className="flex justify-between text-xs border-b pb-1"
                      >
                        <span>
                          {r.order_name}{" "}
                          <span className="text-muted-foreground">{r.refund_date}</span>
                        </span>
                        <span className="font-mono text-red-700">
                          -{Number(r.amount).toFixed(2)}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Column 2: Payout Detail */}
            <div>
              <p className="text-xs font-medium uppercase text-muted-foreground mb-2">
                Payout Breakdown
              </p>
              <div className="space-y-1 text-xs">
                <div className="flex justify-between">
                  <span>Gross Amount</span>
                  <span className="font-mono">{formatMoney(p.gross_amount, p.currency)}</span>
                </div>
                <div className="flex justify-between text-muted-foreground">
                  <span>Processing Fees</span>
                  <span className="font-mono">-{formatMoney(p.fees, p.currency)}</span>
                </div>
                <div className="flex justify-between font-medium border-t pt-1">
                  <span>Net Deposit</span>
                  <span className="font-mono">{formatMoney(p.net_amount, p.currency)}</span>
                </div>
                <div className="flex justify-between mt-2 text-muted-foreground">
                  <span>Status</span>
                  <span className="capitalize">{p.shopify_status}</span>
                </div>
                <div className="flex justify-between text-muted-foreground">
                  <span>Payout ID</span>
                  <span className="font-mono">{p.shopify_payout_id}</span>
                </div>
              </div>
            </div>

            {/* Column 3: Bank Detail */}
            <div>
              <p className="text-xs font-medium uppercase text-muted-foreground mb-2">
                Bank Statement
              </p>
              {group.bank_deposit ? (
                <div className="space-y-1 text-xs">
                  <div className="flex justify-between">
                    <span>Date</span>
                    <span>{group.bank_deposit.line_date}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Amount</span>
                    <span className="font-mono">
                      {formatMoney(group.bank_deposit.amount, p.currency)}
                    </span>
                  </div>
                  {group.bank_deposit.description && (
                    <div className="flex justify-between">
                      <span>Description</span>
                      <span className="truncate max-w-[150px]">
                        {group.bank_deposit.description}
                      </span>
                    </div>
                  )}
                  {group.bank_deposit.reference && (
                    <div className="flex justify-between">
                      <span>Reference</span>
                      <span className="truncate max-w-[150px]">
                        {group.bank_deposit.reference}
                      </span>
                    </div>
                  )}
                  <div className="flex items-center gap-1 mt-2 text-green-600">
                    <CheckCircle2 className="h-3 w-3" />
                    <span>Verified: Payout matches bank deposit</span>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-1 text-xs text-red-600 mt-2">
                  <XCircle className="h-3 w-3" />
                  <span>No matching bank deposit found</span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale ?? "en", ["common"])),
  },
});
