import { useState, useEffect } from "react";
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
} from "@/services/stripe.service";

function fmt(amount: string | number, currency = "USD") {
  const n = typeof amount === "string" ? parseFloat(amount) : amount;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(n);
}

export default function StripeDashboardPage() {
  const [account, setAccount] = useState<StripeAccount | null>(null);
  const [charges, setCharges] = useState<StripeChargeItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [acctRes, chargesRes] = await Promise.allSettled([
          stripeService.getAccount(),
          stripeService.getCharges(),
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
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const isConnected = account?.status === "ACTIVE";

  const stats = {
    total: charges.length,
    processed: charges.filter((c) => c.status === "PROCESSED").length,
    errors: charges.filter((c) => c.status === "ERROR").length,
    revenue: charges
      .filter((c) => c.status === "PROCESSED")
      .reduce((sum, c) => sum + Number(c.amount), 0),
    fees: charges
      .filter((c) => c.status === "PROCESSED")
      .reduce((sum, c) => sum + Number(c.fee), 0),
    currency: charges[0]?.currency ?? "USD",
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
                      <p className="text-sm text-muted-foreground">
                        {account.livemode ? "Live mode" : "Test mode"}
                      </p>
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
                  <div className="text-2xl font-bold">{fmt(stats.revenue, stats.currency)}</div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Processing Fees</CardTitle>
                  <AlertCircle className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-red-400">{fmt(stats.fees, stats.currency)}</div>
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
                              {new Date(charge.charge_date).toLocaleDateString()}
                              {charge.customer_name ? ` · ${charge.customer_name}` : ""}
                            </p>
                          </div>
                        </div>
                        <span className="text-sm font-mono font-medium">
                          {fmt(charge.amount, charge.currency)}
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
