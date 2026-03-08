import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { Settings, Save } from "lucide-react";
import { useState, useEffect } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useClinicAccountMapping, useUpdateClinicAccountMapping } from "@/queries/useClinic";
import { useAccounts } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";

export default function ClinicSettingsPage() {
  const { data: mapping, isLoading: mappingLoading } = useClinicAccountMapping();
  const { data: accounts, isLoading: accountsLoading } = useAccounts();
  const updateMapping = useUpdateClinicAccountMapping();
  const { toast } = useToast();
  const [form, setForm] = useState<Record<string, number | null>>({});

  useEffect(() => {
    if (mapping) {
      const initial: Record<string, number | null> = {};
      mapping.forEach((m) => { initial[m.role] = m.account_id; });
      setForm(initial);
    }
  }, [mapping]);

  const handleSave = async () => {
    if (!mapping) return;
    try {
      const payload = mapping.map((m) => ({
        ...m,
        account_id: form[m.role] ?? null,
      }));
      await updateMapping.mutateAsync(payload);
      toast({ title: "Account mappings saved" });
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Failed to save mappings", variant: "destructive" });
    }
  };

  const postableAccounts = accounts?.filter((a) => !a.is_header && a.status === "ACTIVE") || [];
  const isLoading = mappingLoading || accountsLoading;

  const ROLE_LABELS: Record<string, string> = {
    ACCOUNTS_RECEIVABLE: "Accounts Receivable (Patient Receivable)",
    CONSULTATION_REVENUE: "Consultation Revenue",
    CASH_BANK: "Cash / Bank Account",
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Clinic Settings"
          subtitle="Configure account mappings for the clinic module"
          actions={
            <Button onClick={handleSave} disabled={updateMapping.isPending}>
              <Save className="mr-2 h-4 w-4" />
              {updateMapping.isPending ? "Saving..." : "Save Mappings"}
            </Button>
          }
        />

        {isLoading ? (
          <LoadingSpinner />
        ) : (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Settings className="h-5 w-5" />
                Account Role Mappings
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground mb-4">
                Map each clinic accounting role to a GL account from your Chart of Accounts.
                These accounts are used when clinic invoices and payments generate journal entries.
              </p>
              {mapping?.map((m) => (
                <div key={m.role}>
                  <Label>{ROLE_LABELS[m.role] || m.role}</Label>
                  <select
                    className="w-full border rounded-md px-3 py-2 text-sm mt-1"
                    value={form[m.role] ?? ""}
                    onChange={(e) => setForm({ ...form, [m.role]: e.target.value ? Number(e.target.value) : null })}
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

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: { ...(await serverSideTranslations(locale ?? "en", ["common"])) },
});
