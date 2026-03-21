import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { Settings, Save, Loader2 } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  coreAccountMappingService,
  CoreAccountMapping,
} from "@/services/accounts.service";
import { useAccounts } from "@/queries/useAccounts";

const ROLE_LABELS: Record<string, { label: string; description: string }> = {
  FX_GAIN: {
    label: "Unrealized FX Gain",
    description: "Account for unrealized foreign exchange gains during currency revaluation",
  },
  FX_LOSS: {
    label: "Unrealized FX Loss",
    description: "Account for unrealized foreign exchange losses during currency revaluation",
  },
  FX_ROUNDING: {
    label: "FX Rounding Differences",
    description: "Account for penny rounding differences caused by per-line FX conversion",
  },
};

export default function AccountingSettingsPage() {
  const { toast } = useToast();
  const { data: accounts } = useAccounts();

  const [mappings, setMappings] = useState<CoreAccountMapping[]>([]);
  const [mappingForm, setMappingForm] = useState<Record<string, number | null>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const fetchMappings = async () => {
    setLoading(true);
    try {
      const { data } = await coreAccountMappingService.get();
      setMappings(data);
      const initial: Record<string, number | null> = {};
      data.forEach((m) => {
        initial[m.role] = m.account_id;
      });
      setMappingForm(initial);
    } catch {
      // Mapping not available yet
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = Object.entries(mappingForm).map(([role, account_id]) => ({
        role,
        account_id,
      }));
      await coreAccountMappingService.update(payload);
      toast({ title: "Account mappings saved." });
      fetchMappings();
    } catch {
      toast({ title: "Failed to save mappings.", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    fetchMappings();
  }, []);

  const postableAccounts =
    accounts?.filter((a) => !a.is_header && a.status === "ACTIVE") || [];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Accounting Settings"
          subtitle="Configure core account mappings used by the accounting engine"
        />

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Settings className="h-5 w-5" />
                FX Account Mappings
              </CardTitle>
              <Button onClick={handleSave} disabled={saving} size="sm">
                <Save className="me-2 h-4 w-4" />
                {saving ? "Saving..." : "Save Mappings"}
              </Button>
            </CardHeader>
            <CardContent className="space-y-5">
              <p className="text-sm text-muted-foreground">
                Map each FX accounting role to a GL account from your Chart of Accounts.
                These accounts are used during currency revaluation and multi-currency
                journal entry creation.
              </p>
              {mappings.map((m) => {
                const meta = ROLE_LABELS[m.role];
                return (
                  <div key={m.role}>
                    <Label>{meta?.label || m.role}</Label>
                    {meta?.description && (
                      <p className="text-xs text-muted-foreground mb-1">
                        {meta.description}
                      </p>
                    )}
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
                );
              })}
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
