import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  Building2,
  Plus,
  Loader2,
  ArrowLeftRight,
  Upload,
  MoreVertical,
  Trash2,
  Edit,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { bankService, BankAccount } from "@/services/bank.service";
import { useAccounts } from "@/queries/useAccounts";
import Link from "next/link";

export default function BankAccountsPage() {
  const { toast } = useToast();
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);

  // Form state
  const [bankName, setBankName] = useState("");
  const [accountName, setAccountName] = useState("");
  const [last4, setLast4] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [glAccountId, setGlAccountId] = useState<number | null>(null);

  const { data: glAccounts } = useAccounts();
  const postableAccounts =
    glAccounts?.filter((a) => !a.is_header && a.status === "ACTIVE") || [];

  async function loadAccounts() {
    setLoading(true);
    try {
      const { data } = await bankService.getAccounts();
      setAccounts(data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAccounts();
  }, []);

  async function handleCreate() {
    if (!bankName.trim() || !accountName.trim()) {
      toast({ title: "Bank name and account name are required.", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      await bankService.createAccount({
        bank_name: bankName.trim(),
        account_name: accountName.trim(),
        account_number_last4: last4.trim(),
        currency,
        gl_account_id: glAccountId,
      });
      toast({ title: "Bank account created." });
      setBankName("");
      setAccountName("");
      setLast4("");
      setCurrency("USD");
      setGlAccountId(null);
      setShowForm(false);
      loadAccounts();
    } catch {
      toast({ title: "Failed to create account.", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this bank account and all its imported transactions?")) return;
    try {
      await bankService.deleteAccount(id);
      toast({ title: "Bank account deleted." });
      loadAccounts();
    } catch {
      toast({ title: "Failed to delete.", variant: "destructive" });
    }
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Bank Accounts"
          subtitle="Manage your bank accounts for statement import and reconciliation"
        />

        {/* Add Account Button */}
        <div className="flex justify-end">
          <Button onClick={() => setShowForm(!showForm)}>
            <Plus className="me-2 h-4 w-4" />
            Add Bank Account
          </Button>
        </div>

        {/* Create Form */}
        {showForm && (
          <Card>
            <CardHeader>
              <CardTitle>New Bank Account</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Bank Name</Label>
                  <Input
                    value={bankName}
                    onChange={(e) => setBankName(e.target.value)}
                    placeholder="e.g. CIB, Alex Bank, Chase"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Account Name</Label>
                  <Input
                    value={accountName}
                    onChange={(e) => setAccountName(e.target.value)}
                    placeholder="e.g. Main Operating Account"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Last 4 Digits (optional)</Label>
                  <Input
                    value={last4}
                    onChange={(e) => setLast4(e.target.value.slice(0, 4))}
                    placeholder="1234"
                    maxLength={4}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Currency</Label>
                  <Input
                    value={currency}
                    onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))}
                    placeholder="USD"
                    maxLength={3}
                  />
                </div>
                <div className="space-y-1.5 sm:col-span-2">
                  <Label>Linked GL Account (optional)</Label>
                  <select
                    className="w-full border rounded-md px-3 py-2 text-sm"
                    value={glAccountId ?? ""}
                    onChange={(e) =>
                      setGlAccountId(e.target.value ? Number(e.target.value) : null)
                    }
                  >
                    <option value="">— Not linked —</option>
                    {postableAccounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.code} — {a.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <Button variant="outline" onClick={() => setShowForm(false)}>
                  Cancel
                </Button>
                <Button onClick={handleCreate} disabled={saving}>
                  {saving && <Loader2 className="me-2 h-4 w-4 animate-spin" />}
                  Create Account
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Accounts List */}
        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : accounts.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-12 text-center">
              <Building2 className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">No Bank Accounts</h3>
              <p className="text-sm text-muted-foreground mb-4 max-w-md">
                Add a bank account to start importing statements and reconciling
                transactions.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {accounts.map((acct) => (
              <Card key={acct.id} className="relative">
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                        <Building2 className="h-5 w-5 text-primary" />
                      </div>
                      <div>
                        <CardTitle className="text-base">{acct.account_name}</CardTitle>
                        <p className="text-sm text-muted-foreground">
                          {acct.bank_name}
                          {acct.account_number_last4 && ` ••${acct.account_number_last4}`}
                        </p>
                      </div>
                    </div>
                    <Badge variant={acct.status === "ACTIVE" ? "success" : "secondary"}>
                      {acct.currency}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div>
                      <p className="text-xl font-bold">{acct.transaction_count}</p>
                      <p className="text-xs text-muted-foreground">Transactions</p>
                    </div>
                    <div>
                      <p className="text-xl font-bold">{acct.statement_count}</p>
                      <p className="text-xs text-muted-foreground">Statements</p>
                    </div>
                    <div>
                      <p className="text-xl font-bold text-amber-600">{acct.unmatched_count}</p>
                      <p className="text-xs text-muted-foreground">Unmatched</p>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Link href={`/banking/transactions?account=${acct.id}`} className="flex-1">
                      <Button variant="outline" size="sm" className="w-full">
                        <ArrowLeftRight className="me-2 h-3.5 w-3.5" />
                        Transactions
                      </Button>
                    </Link>
                    <Link href={`/banking/import?account=${acct.id}`} className="flex-1">
                      <Button variant="outline" size="sm" className="w-full">
                        <Upload className="me-2 h-3.5 w-3.5" />
                        Import
                      </Button>
                    </Link>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(acct.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
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
