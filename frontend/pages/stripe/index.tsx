import { useState, useEffect } from "react";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import {
  CreditCard,
  Settings,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ArrowRight,
  Receipt,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/common";
import {
  stripeService,
  StripeAccount,
  StripeChargeItem,
  StripeDashboardSummary,
  StripeMoneyByCurrency,
} from "@/services/stripe.service";

export default function StripeDashboardPage() {
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();
  const [account, setAccount] = useState<StripeAccount | null>(null);
  const [charges, setCharges] = useState<StripeChargeItem[]>([]);
  const [summary, setSummary] = useState<StripeDashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [acctRes, chargesRes, summaryRes] = await Promise.allSettled([
          stripeService.getAccount(),
          stripeService.getCharges(),
          stripeService.getDashboardSummary(),
        ]);

        if (acctRes.status === "fulfilled") {
          const d = acctRes.value.data;
          if ("connected" in d && d.connected === false) {
            setAccount(null);
          } else {
            setAccount(d as StripeAccount);
          }
        }

        if (chargesRes.status === "fulfilled") {
          setCharges(chargesRes.value.data);
        }
        if (summaryRes.status === "fulfilled") {
          setSummary(summaryRes.value.data);
        }
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const isConnected = account?.status === "ACTIVE";

  // A143: counts and revenue come from the server-side summary (all charges,
  // per-currency — the charges list is capped at 100 rows). Fees come from
  // canonical payout headers: charge rows carry fee=0 by design, since real
  // Stripe fees only become known at payout time. If the summary endpoint is
  // unavailable, fall back to charge-derived counts/revenue and show no fee
  // figure rather than a false 0.
  const stats = summary
    ? {
        total: summary.charges.total,
        processed: summary.charges.processed,
        errors: summary.charges.errors,
        revenue: summary.charges.revenue,
        fees: summary.fees as StripeMoneyByCurrency[],
      }
    : {
        total: charges.length,
        processed: charges.filter((c) => c.status === "PROCESSED").length,
        errors: charges.filter((c) => c.status === "ERROR").length,
        revenue: Object.entries(
          charges
            .filter((c) => c.status === "PROCESSED")
            .reduce<Record<string, number>>((acc, c) => {
              acc[c.currency] = (acc[c.currency] ?? 0) + Number(c.amount);
              return acc;
            }, {})
        ).map(([currency, amount]) => ({ currency, amount: String(amount) })),
        fees: null,
      };

  // First entry renders big, the rest as smaller lines — multi-currency
  // amounts are listed per currency, never summed across currencies.
  // null = the summary endpoint was unavailable: show a dash, not a false 0.
  const renderMoneyTile = (entries: StripeMoneyByCurrency[] | null, tone = "") => {
    if (entries === null) {
      return <div className={`text-2xl font-bold ${tone}`}>&mdash;</div>;
    }
    if (entries.length === 0) {
      return <div className={`text-2xl font-bold ${tone}`}>{formatCurrency(0)}</div>;
    }
    const [first, ...rest] = entries;
    return (
      <>
        <div className={`text-2xl font-bold ${tone}`}>
          {formatCurrency(first.amount, first.currency)}
        </div>
        {rest.map((e) => (
          <div key={e.currency} className={`text-sm font-semibold ${tone || "text-muted-foreground"}`}>
            {formatCurrency(e.amount, e.currency)}
          </div>
        ))}
      </>
    );
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader title="Stripe" subtitle="Payment processing overview" />

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : !isConnected ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-12 text-center">
              <CreditCard className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">No Stripe Account Connected</h3>
              <p className="text-sm text-muted-foreground mb-6 max-w-md">
                Connect your Stripe account to automatically track charges, fees,
                and create journal entries from your payments.
              </p>
              <Link href="/stripe/settings">
                <Button>
                  <Settings className="me-2 h-4 w-4" />
                  Go to Settings
                </Button>
              </Link>
            </CardContent>
          </Card>
        ) : (
          <>
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-green-100">
                      <CheckCircle2 className="h-5 w-5 text-green-600" />
                    </div>
                    <div>
                      <p className="font-semibold">
                        Connected: {account.display_name || account.stripe_account_id}
                      </p>
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <span>{account.livemode ? "Live mode" : "Test mode"}</span>
                        <span>·</span>
                        {account.webhook_secret_configured ? (
                          <span className="text-green-600">Webhook configured</span>
                        ) : (
                          <Link href="/stripe/settings" className="text-yellow-600 underline">
                            Webhook not configured
                          </Link>
                        )}
                      </div>
                    </div>
                  </div>
                  <Link href="/stripe/settings">
                    <Button variant="outline" size="sm">
                      <Settings className="me-2 h-4 w-4" />
                      Settings
                    </Button>
                  </Link>
                </div>
              </CardContent>
            </Card>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total Charges</CardTitle>
                  <Receipt className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.total}</div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Processed</CardTitle>
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.processed}</div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Revenue</CardTitle>
                  <CreditCard className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  {renderMoneyTile(stats.revenue)}
                  <p className="mt-1 text-xs text-muted-foreground">
                    Gross charge volume, before fees
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Processing Fees</CardTitle>
                  <AlertCircle className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  {renderMoneyTile(stats.fees, "text-red-400")}
                  <p className="mt-1 text-xs text-muted-foreground">
                    Actual fees from Stripe payout reports, as posted to your fee account
                  </p>
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle>Recent Charges</CardTitle>
                <Link href="/stripe/charges">
                  <Button variant="ghost" size="sm">
                    View All
                    <ArrowRight className="ms-2 h-4 w-4" />
                  </Button>
                </Link>
              </CardHeader>
              <CardContent>
                {charges.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-6 text-center">
                    No charges received yet.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {charges.slice(0, 5).map((charge) => (
                      <div
                        key={charge.id}
                        className="flex items-center justify-between rounded-lg border p-3"
                      >
                        <div className="flex items-center gap-3">
                          <div className="flex h-8 w-8 items-center justify-center rounded bg-muted">
                            <CreditCard className="h-4 w-4 text-muted-foreground" />
                          </div>
                          <div>
                            <p className="text-sm font-medium">{charge.description || charge.stripe_charge_id}</p>
                            <p className="text-xs text-muted-foreground">
                              {formatDate(charge.charge_date)}
                              {charge.customer_name ? ` · ${charge.customer_name}` : ""}
                            </p>
                          </div>
                        </div>
                        <span className="text-sm font-mono font-medium">
                          {formatCurrency(charge.amount, charge.currency)}
                        </span>
                      </div>
                    ))}
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

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
