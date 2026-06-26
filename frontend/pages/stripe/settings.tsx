import { useState, useEffect, useMemo } from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
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
  // Settlement-drain roles the connect seed also maps (platform_stripe). Without
  // these the rows would render the raw role key.
  EXPECTED_BANK_DEPOSIT: "Expected Bank Deposit",
  SALES_RETURNS: "Sales Returns / Failed Delivery",
};

export default function StripeSettingsPage() {
  const { toast } = useToast();
  const [account, setAccount] = useState<StripeAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [disconnecting, setDisconnecting] = useState(false);

  // Connect form (ADR-0002 S1): merchant pastes a restricted READ key (rk_…).
  const [apiKey, setApiKey] = useState("");
  const [connecting, setConnecting] = useState(false);

  // Account mapping
  const accountsQuery = useAccounts();
  const accounts = accountsQuery.data;
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

  async function handleConnect() {
    const key = apiKey.trim();
    if (!key) {
      toast({
        title: "Enter your Stripe restricted read-only key (rk_…).",
        variant: "destructive",
      });
      return;
    }
    setConnecting(true);
    try {
      await stripeService.connect(key);
      toast({
        title: "Stripe connected.",
        description: "Payouts will sync shortly.",
      });
      await loadAccount();
      // connect seeds the default platform_stripe ModuleAccountMappings
      // server-side. Refetch them so the Account Mappings form reflects the
      // seeded accounts — otherwise it keeps the pre-connect all-null rows and
      // a "Save Mappings" click would PUT those nulls back, wiping the seeded
      // mapping and leaving Stripe accounting unmapped (Codex P1).
      await fetchMappings();
      // The seed also creates Stripe-specific GL accounts (Stripe Clearing 11510,
      // Expected Bank Deposit 11610) that weren't in the accounts list when this
      // page mounted. Refetch it so those accounts are selectable in the mapping
      // dropdowns — otherwise a <select> whose value is a brand-new account id has
      // no matching <option> and falsely renders as "Not mapped".
      await accountsQuery.refetch();
    } catch (err) {
      // The backend rejects sk_/pk_ and invalid/under-scoped keys with a clear,
      // user-facing message — surface it verbatim instead of a generic error.
      toast({ title: getErrorMessage(err), variant: "destructive" });
    } finally {
      // Never retain the raw secret in component state / the DOM after an
      // attempt — clearing on every outcome (not just success) keeps cleartext
      // out of the Sentry session-replay-on-error window.
      setApiKey("");
      setConnecting(false);
    }
  }

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

  // Postable GL accounts (active, non-header), sorted by code — the base option
  // set every mapping row offers. Each row may additionally fold in its OWN
  // currently-mapped account when that account isn't postable (see optionsForRow).
  const postableAccounts = useMemo(
    () =>
      (accounts ?? [])
        .filter((a) => !a.is_header && a.status === "ACTIVE")
        .map((a) => ({ id: a.id, code: a.code, name: a.name }))
        .sort((a, b) => a.code.localeCompare(b.code)),
    [accounts],
  );
  const postableIds = useMemo(() => new Set(postableAccounts.map((a) => a.id)), [postableAccounts]);

  // Options for one mapping row: postable accounts + this row's currently-mapped
  // account when it isn't postable (freshly seeded at connect before the accounts
  // query refetches, or filtered out as inactive/header). Scoped to THIS row so a
  // display-only account never becomes a newly-selectable choice for another role
  // — the PUT only checks company ownership, not active/non-header (Codex P2).
  // Without it a <select> whose value matches no <option> silently renders as
  // "Not mapped" even though the role IS mapped server-side.
  const optionsForRow = (m: StripeAccountMapping) => {
    if (m.account_id == null || postableIds.has(m.account_id)) return postableAccounts;
    return [...postableAccounts, { id: m.account_id, code: m.account_code, name: m.account_name }].sort(
      (a, b) => a.code.localeCompare(b.code),
    );
  };

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
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Paste a Stripe <strong>restricted</strong> API key to let Nxentra read your
                payouts and balance transactions for reconciliation. Nxentra is read-only and
                never receives a key that can move money.
              </p>
              <div className="flex flex-col gap-3 max-w-lg sm:flex-row sm:items-end">
                <div className="flex-1 space-y-1.5">
                  <Label htmlFor="stripe-key">Restricted API key</Label>
                  <Input
                    id="stripe-key"
                    type="password"
                    autoComplete="off"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="rk_live_…"
                  />
                </div>
                <div className="flex items-end">
                  <Button onClick={handleConnect} disabled={connecting}>
                    {connecting && <Loader2 className="me-2 h-4 w-4 animate-spin" />}
                    Connect
                  </Button>
                </div>
              </div>
              <div className="rounded-lg border p-4 bg-muted/30 space-y-1">
                <p className="text-sm font-medium">How to create a restricted read key</p>
                <p className="text-xs text-muted-foreground">
                  In Stripe: <strong>Developers → API keys → Create restricted key</strong>. Set{" "}
                  <strong>Balance</strong> and <strong>Payouts</strong> to <strong>Read</strong>{" "}
                  (leave everything else None), then paste the{" "}
                  <code className="bg-muted px-1 py-0.5 rounded">rk_…</code> key here.
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
                    {optionsForRow(m).map((a) => (
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
