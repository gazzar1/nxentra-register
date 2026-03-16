import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  CreditCard,
  CheckCircle2,
  Loader2,
  AlertTriangle,
  Unplug,
  Settings,
  Save,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  stripeService,
  StripeAccount,
  StripeAccountMapping,
} from "@/services/stripe.service";
import { useAccounts } from "@/queries/useAccounts";

const ROLE_LABELS: Record<string, string> = {
  SALES_REVENUE: "Sales Revenue",
  STRIPE_CLEARING: "Stripe Clearing",
  PAYMENT_PROCESSING_FEES: "Payment Processing Fees",
  SALES_TAX_PAYABLE: "Sales Tax Payable",
  CASH_BANK: "Cash / Bank Account",
  CHARGEBACK_EXPENSE: "Chargeback Expense",
};

export default function StripeSettingsPage() {
  const { toast } = useToast();
  const [account, setAccount] = useState<StripeAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [disconnecting, setDisconnecting] = useState(false);

  // Account mapping
  const { data: accounts } = useAccounts();
  const [mappings, setMappings] = useState<StripeAccountMapping[]>([]);
  const [mappingForm, setMappingForm] = useState<Record<string, number | null>>({});
  const [savingMappings, setSavingMappings] = useState(false);

  async function loadAccount() {
    setLoading(true);
    try {
      const { data } = await stripeService.getAccount();
      if ("connected" in data && data.connected === false) {
        setAccount(null);
      } else {
        setAccount(data as StripeAccount);
      }
    } finally {
      setLoading(false);
    }
  }

  async function fetchMappings() {
    try {
      const { data } = await stripeService.getAccountMapping();
      setMappings(data);
      const initial: Record<string, number | null> = {};
      data.forEach((m) => { initial[m.role] = m.account_id; });
      setMappingForm(initial);
    } catch {
      // Mapping not available yet
    }
  }

  useEffect(() => {
    loadAccount();
    fetchMappings();
  }, []);

  async function handleDisconnect() {
    if (!confirm("Are you sure you want to disconnect this Stripe account?")) return;
    setDisconnecting(true);
    try {
      await stripeService.disconnect();
      toast({ title: "Stripe account disconnected." });
      await loadAccount();
    } catch {
      toast({ title: "Failed to disconnect.", variant: "destructive" });
    } finally {
      setDisconnecting(false);
    }
  }

  async function handleSaveMappings() {
    setSavingMappings(true);
    try {
      const payload = mappings.map((m) => ({
        ...m,
        account_id: mappingForm[m.role] ?? null,
      }));
      await stripeService.updateAccountMapping(payload);
      toast({ title: "Account mappings saved." });
    } catch {
      toast({ title: "Failed to save mappings.", variant: "destructive" });
    } finally {
      setSavingMappings(false);
    }
  }

  const isConnected = account?.status === "ACTIVE";
  const postableAccounts =
    accounts?.filter((a) => !a.is_header && a.status === "ACTIVE") || [];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Stripe Settings"
          subtitle="Manage your Stripe integration"
        />

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : !isConnected ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CreditCard className="h-5 w-5" />
                Connect Stripe
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground mb-4">
                Stripe integration requires setting up a webhook endpoint in your Stripe dashboard.
                Contact your administrator to configure the connection.
              </p>
              <div className="rounded-lg border p-4 bg-muted/30">
                <p className="text-sm font-medium mb-2">Webhook Setup</p>
                <p className="text-xs text-muted-foreground">
                  Point your Stripe webhook to: <code className="bg-muted px-1 py-0.5 rounded">/api/platforms/stripe/webhooks/</code>
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Events: charge.captured, charge.refunded, payout.paid, charge.dispute.created
                </p>
              </div>
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Connection Status */}
            <Card>
              <CardHeader>
                <CardTitle>Connection Status</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-green-100">
                      <CheckCircle2 className="h-5 w-5 text-green-600" />
                    </div>
                    <div>
                      <p className="font-semibold">
                        {account.display_name || account.stripe_account_id}
                      </p>
                      <div className="flex items-center gap-2 mt-0.5">
                        <Badge variant={account.livemode ? "success" : "warning"}>
                          {account.livemode ? "Live" : "Test"}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          Connected {new Date(account.created_at).toLocaleDateString()}
                        </span>
                      </div>
                    </div>
                  </div>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={handleDisconnect}
                    disabled={disconnecting}
                  >
                    {disconnecting ? (
                      <Loader2 className="me-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Unplug className="me-2 h-4 w-4" />
                    )}
                    Disconnect
                  </Button>
                </div>
              </CardContent>
            </Card>

            {account.error_message && (
              <Card>
                <CardContent className="pt-6">
                  <div className="flex items-start gap-3 text-yellow-600">
                    <AlertTriangle className="h-5 w-5 mt-0.5 shrink-0" />
                    <div>
                      <p className="font-medium">Connection Issue</p>
                      <p className="text-sm">{account.error_message}</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}
          </>
        )}

        {/* Account Mappings — always show when mappings exist */}
        {mappings.length > 0 && (
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Settings className="h-5 w-5" />
                Account Mappings
              </CardTitle>
              <Button onClick={handleSaveMappings} disabled={savingMappings} size="sm">
                <Save className="me-2 h-4 w-4" />
                {savingMappings ? "Saving..." : "Save Mappings"}
              </Button>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Map each Stripe accounting role to a GL account from your Chart of Accounts.
                These accounts are used when charges and refunds generate journal entries.
              </p>
              {mappings.map((m) => (
                <div key={m.role}>
                  <Label>{ROLE_LABELS[m.role] || m.role}</Label>
                  <select
                    className="w-full border rounded-md px-3 py-2 text-sm mt-1"
                    value={mappingForm[m.role] ?? ""}
                    onChange={(e) =>
                      setMappingForm({
                        ...mappingForm,
                        [m.role]: e.target.value ? Number(e.target.value) : null,
                      })
                    }
                  >
                    <option value="">— Not mapped —</option>
                    {postableAccounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.code} — {a.name}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
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
