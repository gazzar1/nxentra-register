import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import {
  ShoppingCart,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Loader2,
  Unplug,
  Webhook,
  Save,
  Settings,
  RefreshCw,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  shopifyService,
  ShopifyStore,
  ShopifyAccountMapping,
} from "@/services/shopify.service";
import { useAccounts } from "@/queries/useAccounts";

export default function ShopifySettingsPage() {
  const { t } = useTranslation(["common"]);
  const router = useRouter();
  const { toast } = useToast();

  const [store, setStore] = useState<ShopifyStore | null>(null);
  const [loading, setLoading] = useState(true);
  const [shopDomain, setShopDomain] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [registeringWebhooks, setRegisteringWebhooks] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [syncingPayouts, setSyncingPayouts] = useState(false);

  // Account mapping
  const { data: accounts } = useAccounts();
  const [mappings, setMappings] = useState<ShopifyAccountMapping[]>([]);
  const [mappingForm, setMappingForm] = useState<Record<string, number | null>>({});
  const [savingMappings, setSavingMappings] = useState(false);

  const fetchStore = async () => {
    setLoading(true);
    try {
      const { data } = await shopifyService.getStore();
      const d = data as any;
      if (!d.connected) {
        setStore(null);
      } else if (d.stores && d.stores.length > 0) {
        setStore(d.stores[0] as ShopifyStore);
      } else if (d.status) {
        setStore(d as ShopifyStore);
      } else {
        setStore(null);
      }
    } catch {
      setStore(null);
    } finally {
      setLoading(false);
    }
  };

  const fetchMappings = async () => {
    try {
      const { data } = await shopifyService.getAccountMapping();
      setMappings(data);
      const initial: Record<string, number | null> = {};
      data.forEach((m) => { initial[m.role] = m.account_id; });
      setMappingForm(initial);
    } catch {
      // Mapping not available yet
    }
  };

  const handleSaveMappings = async () => {
    setSavingMappings(true);
    try {
      const payload = mappings.map((m) => ({
        ...m,
        account_id: mappingForm[m.role] ?? null,
      }));
      await shopifyService.updateAccountMapping(payload);
      toast({ title: "Account mappings saved." });
    } catch {
      toast({ title: "Failed to save mappings.", variant: "destructive" });
    } finally {
      setSavingMappings(false);
    }
  };

  useEffect(() => {
    fetchStore();
    fetchMappings();

    // Check for OAuth callback result
    if (router.query.connected === "true") {
      toast({ title: "Shopify store connected successfully!" });
      router.replace("/shopify/settings", undefined, { shallow: true });
    } else if (router.query.error) {
      toast({
        title: "Connection failed",
        description: String(router.query.error),
        variant: "destructive",
      });
      router.replace("/shopify/settings", undefined, { shallow: true });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleConnect = async () => {
    if (!shopDomain.trim()) {
      toast({ title: "Please enter your Shopify store domain.", variant: "destructive" });
      return;
    }
    setConnecting(true);
    try {
      const { data } = await shopifyService.install(shopDomain.trim());
      // Redirect to Shopify OAuth
      window.location.href = data.url;
    } catch {
      toast({ title: "Failed to start connection.", variant: "destructive" });
      setConnecting(false);
    }
  };

  const handleRegisterWebhooks = async () => {
    setRegisteringWebhooks(true);
    try {
      const { data } = await shopifyService.registerWebhooks();
      if (data.errors && data.errors.length > 0) {
        toast({
          title: `Registered ${data.registered.length} webhooks with ${data.errors.length} errors`,
          variant: "destructive",
        });
      } else {
        toast({ title: "Webhooks registered successfully!" });
      }
      fetchStore();
    } catch {
      toast({ title: "Failed to register webhooks.", variant: "destructive" });
    } finally {
      setRegisteringWebhooks(false);
    }
  };

  const handleDisconnect = async () => {
    if (!confirm("Are you sure you want to disconnect your Shopify store?")) return;
    setDisconnecting(true);
    try {
      await shopifyService.disconnect();
      toast({ title: "Shopify store disconnected." });
      setStore(null);
    } catch {
      toast({ title: "Failed to disconnect.", variant: "destructive" });
    } finally {
      setDisconnecting(false);
    }
  };

  const handleSyncPayouts = async () => {
    setSyncingPayouts(true);
    try {
      const { data } = await shopifyService.syncPayouts();
      toast({
        title: `Payout sync complete: ${data.created} new, ${data.skipped} already synced`,
      });
      fetchStore(); // refresh last_sync_at
    } catch {
      toast({ title: "Failed to sync payouts.", variant: "destructive" });
    } finally {
      setSyncingPayouts(false);
    }
  };

  const isConnected = store?.status === "ACTIVE";

  const postableAccounts =
    accounts?.filter((a) => !a.is_header && a.status === "ACTIVE") || [];

  const ROLE_LABELS: Record<string, string> = {
    SALES_REVENUE: "Sales Revenue",
    SHOPIFY_CLEARING: "Shopify Clearing",
    SALES_TAX_PAYABLE: "Sales Tax Payable",
    SHIPPING_REVENUE: "Shipping Revenue",
    SALES_DISCOUNTS: "Sales Discounts",
    CASH_BANK: "Cash / Bank Account",
    PAYMENT_PROCESSING_FEES: "Payment Processing Fees",
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Shopify Integration"
          subtitle="Connect your Shopify store to automatically sync orders and create journal entries"
        />

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : !isConnected ? (
          /* ============== Not Connected ============== */
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShoppingCart className="h-5 w-5" />
                Connect Your Shopify Store
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Enter your Shopify store domain to begin the connection process.
                You&apos;ll be redirected to Shopify to authorize the app.
              </p>
              <div className="flex gap-3 max-w-lg">
                <div className="flex-1 space-y-1.5">
                  <Label htmlFor="shop-domain">Store Domain</Label>
                  <Input
                    id="shop-domain"
                    value={shopDomain}
                    onChange={(e) => setShopDomain(e.target.value)}
                    placeholder="my-store.myshopify.com"
                  />
                </div>
                <div className="flex items-end">
                  <Button onClick={handleConnect} disabled={connecting}>
                    {connecting && <Loader2 className="me-2 h-4 w-4 animate-spin" />}
                    Connect
                  </Button>
                </div>
              </div>

              {store?.status === "ERROR" && (
                <div className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                  <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                  <span>{store.error_message || "Connection error. Please try again."}</span>
                </div>
              )}

              {store?.status === "DISCONNECTED" && (
                <div className="flex items-start gap-2 rounded-md bg-muted p-3 text-sm text-muted-foreground">
                  <Unplug className="h-4 w-4 mt-0.5 shrink-0" />
                  <span>Previously connected to {store.shop_domain}. Reconnect to resume syncing.</span>
                </div>
              )}
            </CardContent>
          </Card>
        ) : (
          /* ============== Connected ============== */
          <>
            {/* Connection Status */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-green-500" />
                  Connected Store
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <div>
                    <p className="text-sm text-muted-foreground">Store</p>
                    <p className="font-medium font-mono">{store.shop_domain}</p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Status</p>
                    <p className="font-medium flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-full bg-green-500" />
                      Active
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Webhooks</p>
                    <p className="font-medium flex items-center gap-1.5">
                      {store.webhooks_registered ? (
                        <>
                          <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                          Registered
                        </>
                      ) : (
                        <>
                          <XCircle className="h-3.5 w-3.5 text-yellow-500" />
                          Not registered
                        </>
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Last Sync</p>
                    <p className="font-medium">
                      {store.last_sync_at
                        ? new Date(store.last_sync_at).toLocaleDateString()
                        : "Never"}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Actions */}
            <Card>
              <CardHeader>
                <CardTitle>Actions</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-3">
                {!store.webhooks_registered && (
                  <Button onClick={handleRegisterWebhooks} disabled={registeringWebhooks}>
                    {registeringWebhooks ? (
                      <Loader2 className="me-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Webhook className="me-2 h-4 w-4" />
                    )}
                    Register Webhooks
                  </Button>
                )}
                <Button onClick={handleSyncPayouts} disabled={syncingPayouts}>
                  {syncingPayouts ? (
                    <Loader2 className="me-2 h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="me-2 h-4 w-4" />
                  )}
                  Sync Payouts
                </Button>
                <Button
                  variant="destructive"
                  onClick={handleDisconnect}
                  disabled={disconnecting}
                >
                  {disconnecting ? (
                    <Loader2 className="me-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Unplug className="me-2 h-4 w-4" />
                  )}
                  Disconnect Store
                </Button>
              </CardContent>
            </Card>

            {/* Account Mappings */}
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
                  Map each Shopify accounting role to a GL account from your Chart of Accounts.
                  These accounts are used when orders and refunds generate journal entries.
                </p>
                {mappings.map((m) => (
                  <div key={m.role}>
                    <Label>{ROLE_LABELS[m.role] || m.role}</Label>
                    <select
                      className="w-full border border-input rounded-md bg-background text-foreground px-3 py-2 text-sm mt-1"
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

            {/* How It Works */}
            <Card>
              <CardHeader>
                <CardTitle>How It Works</CardTitle>
              </CardHeader>
              <CardContent>
                <ol className="list-decimal list-inside space-y-2 text-sm text-muted-foreground">
                  <li>When a customer pays for an order, Shopify sends a webhook to Nxentra</li>
                  <li>Nxentra creates a journal entry: DR Shopify Clearing / CR Sales Revenue</li>
                  <li>If the order includes tax, a separate line credits Sales Tax Payable</li>
                  <li>When a refund is issued, Nxentra creates a reversal entry automatically</li>
                  <li>When Shopify sends a payout, Nxentra clears the balance: DR Bank / DR Fees / CR Shopify Clearing</li>
                  <li>All entries appear in your Journal Entries and financial reports</li>
                </ol>
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
