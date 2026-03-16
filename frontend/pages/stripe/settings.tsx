import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  CreditCard,
  CheckCircle2,
  Loader2,
  AlertTriangle,
  Unplug,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { stripeService, StripeAccount } from "@/services/stripe.service";

export default function StripeSettingsPage() {
  const { toast } = useToast();
  const [account, setAccount] = useState<StripeAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [disconnecting, setDisconnecting] = useState(false);

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

  useEffect(() => {
    loadAccount();
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

  const isConnected = account?.status === "ACTIVE";

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
