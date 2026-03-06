import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { useToast } from "@/components/ui/toaster";
import { useCreateLease, useProperties, useUnits, useLessees } from "@/queries/useProperties";
import type { PaymentFrequency, DueDayRule } from "@/types/properties";
import { useState } from "react";

export default function NewLeasePage() {
  const router = useRouter();
  const { toast } = useToast();
  const createLease = useCreateLease();
  const { data: properties } = useProperties();
  const { data: lessees } = useLessees();

  const [selectedPropertyId, setSelectedPropertyId] = useState<number>(0);
  const { data: units } = useUnits(
    selectedPropertyId ? { property: selectedPropertyId } : undefined
  );

  const [form, setForm] = useState({
    contract_no: "",
    property_id: 0,
    unit_id: null as number | null,
    lessee_id: 0,
    start_date: "",
    end_date: "",
    payment_frequency: "monthly" as PaymentFrequency,
    rent_amount: "",
    currency: "SAR",
    grace_days: "0",
    due_day_rule: "first_day" as DueDayRule,
    specific_due_day: "",
    deposit_amount: "0",
    terms_summary: "",
  });

  const handlePropertyChange = (val: number) => {
    setSelectedPropertyId(val);
    setForm({ ...form, property_id: val, unit_id: null });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.property_id || !form.lessee_id) {
      toast({ title: "Error", description: "Select property and lessee.", variant: "destructive" });
      return;
    }
    try {
      await createLease.mutateAsync({
        contract_no: form.contract_no,
        property_id: form.property_id,
        unit_id: form.unit_id,
        lessee_id: form.lessee_id,
        start_date: form.start_date,
        end_date: form.end_date,
        payment_frequency: form.payment_frequency,
        rent_amount: Number(form.rent_amount),
        currency: form.currency,
        grace_days: Number(form.grace_days),
        due_day_rule: form.due_day_rule,
        specific_due_day: form.specific_due_day ? Number(form.specific_due_day) : null,
        deposit_amount: Number(form.deposit_amount),
        terms_summary: form.terms_summary || null,
      });
      toast({ title: "Lease created", description: `Lease ${form.contract_no} created as draft.` });
      router.push("/properties/leases");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to create lease.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <div className="max-w-2xl space-y-6">
        <PageHeader title="New Lease" subtitle="Create a new lease contract (draft)" />

        <Card>
          <CardContent className="p-6">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="text-sm font-medium">Contract Number *</label>
                <Input
                  value={form.contract_no}
                  onChange={(e) => setForm({ ...form, contract_no: e.target.value })}
                  placeholder="e.g. LSE-2026-001"
                  required
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Property *</label>
                  <select
                    value={form.property_id}
                    onChange={(e) => handlePropertyChange(Number(e.target.value))}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    required
                  >
                    <option value={0}>Select property...</option>
                    {properties?.map((p) => (
                      <option key={p.id} value={p.id}>{p.code} - {p.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium">Unit (optional)</label>
                  <select
                    value={form.unit_id ?? ""}
                    onChange={(e) => setForm({ ...form, unit_id: e.target.value ? Number(e.target.value) : null })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="">Whole property</option>
                    {units?.map((u) => (
                      <option key={u.id} value={u.id}>{u.unit_code} - {u.unit_type}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="text-sm font-medium">Lessee *</label>
                <select
                  value={form.lessee_id}
                  onChange={(e) => setForm({ ...form, lessee_id: Number(e.target.value) })}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  required
                >
                  <option value={0}>Select lessee...</option>
                  {lessees?.map((l) => (
                    <option key={l.id} value={l.id}>{l.code} - {l.display_name}</option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Start Date *</label>
                  <Input
                    type="date"
                    value={form.start_date}
                    onChange={(e) => setForm({ ...form, start_date: e.target.value })}
                    required
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">End Date *</label>
                  <Input
                    type="date"
                    value={form.end_date}
                    onChange={(e) => setForm({ ...form, end_date: e.target.value })}
                    required
                  />
                </div>
              </div>

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="text-sm font-medium">Rent Amount *</label>
                  <Input
                    type="number"
                    step="0.01"
                    value={form.rent_amount}
                    onChange={(e) => setForm({ ...form, rent_amount: e.target.value })}
                    required
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Currency</label>
                  <Input
                    value={form.currency}
                    onChange={(e) => setForm({ ...form, currency: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Payment Frequency *</label>
                  <select
                    value={form.payment_frequency}
                    onChange={(e) => setForm({ ...form, payment_frequency: e.target.value as PaymentFrequency })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="monthly">Monthly</option>
                    <option value="quarterly">Quarterly</option>
                    <option value="semiannual">Semi-Annual</option>
                    <option value="annual">Annual</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="text-sm font-medium">Due Day Rule *</label>
                  <select
                    value={form.due_day_rule}
                    onChange={(e) => setForm({ ...form, due_day_rule: e.target.value as DueDayRule })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="first_day">First Day of Period</option>
                    <option value="specific_day">Specific Day</option>
                  </select>
                </div>
                {form.due_day_rule === "specific_day" && (
                  <div>
                    <label className="text-sm font-medium">Specific Day</label>
                    <Input
                      type="number"
                      min="1"
                      max="31"
                      value={form.specific_due_day}
                      onChange={(e) => setForm({ ...form, specific_due_day: e.target.value })}
                    />
                  </div>
                )}
                <div>
                  <label className="text-sm font-medium">Grace Days</label>
                  <Input
                    type="number"
                    value={form.grace_days}
                    onChange={(e) => setForm({ ...form, grace_days: e.target.value })}
                  />
                </div>
              </div>

              <div>
                <label className="text-sm font-medium">Security Deposit</label>
                <Input
                  type="number"
                  step="0.01"
                  value={form.deposit_amount}
                  onChange={(e) => setForm({ ...form, deposit_amount: e.target.value })}
                />
              </div>

              <div>
                <label className="text-sm font-medium">Terms Summary</label>
                <textarea
                  value={form.terms_summary}
                  onChange={(e) => setForm({ ...form, terms_summary: e.target.value })}
                  className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  placeholder="Key terms..."
                />
              </div>

              <div className="flex gap-3 pt-2">
                <Button type="submit" disabled={createLease.isPending}>
                  {createLease.isPending ? "Creating..." : "Create Lease (Draft)"}
                </Button>
                <Button type="button" variant="outline" onClick={() => router.back()}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
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
