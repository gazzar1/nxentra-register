import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { useToast } from "@/components/ui/toaster";
import { useCreateUnit, useProperties } from "@/queries/useProperties";
import type { UnitType } from "@/types/properties";
import { useState } from "react";

const UNIT_TYPES: { value: UnitType; label: string }[] = [
  { value: "apartment", label: "Apartment" },
  { value: "office", label: "Office" },
  { value: "shop", label: "Shop" },
  { value: "warehouse_bay", label: "Warehouse Bay" },
  { value: "room", label: "Room" },
  { value: "parking", label: "Parking" },
  { value: "other", label: "Other" },
];

export default function NewUnitPage() {
  const router = useRouter();
  const { toast } = useToast();
  const createUnit = useCreateUnit();
  const { data: properties } = useProperties();

  const [form, setForm] = useState({
    property_id: router.query.property ? Number(router.query.property) : 0,
    unit_code: "",
    unit_type: "apartment" as UnitType,
    floor: "",
    bedrooms: "" as string,
    bathrooms: "" as string,
    area_sqm: "" as string,
    default_rent: "" as string,
    notes: "",
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.property_id) {
      toast({ title: "Error", description: "Please select a property.", variant: "destructive" });
      return;
    }
    try {
      await createUnit.mutateAsync({
        property_id: form.property_id,
        unit_code: form.unit_code,
        unit_type: form.unit_type,
        floor: form.floor || null,
        bedrooms: form.bedrooms ? Number(form.bedrooms) : null,
        bathrooms: form.bathrooms ? Number(form.bathrooms) : null,
        area_sqm: form.area_sqm ? Number(form.area_sqm) : null,
        default_rent: form.default_rent ? Number(form.default_rent) : null,
        notes: form.notes,
      });
      toast({ title: "Unit created", description: `Unit ${form.unit_code} has been created.` });
      router.push("/properties/units");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to create unit.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <div className="max-w-2xl space-y-6">
        <PageHeader title="New Unit" subtitle="Add a unit to a property" />

        <Card>
          <CardContent className="p-6">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="text-sm font-medium">Property *</label>
                <select
                  value={form.property_id}
                  onChange={(e) => setForm({ ...form, property_id: Number(e.target.value) })}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  required
                >
                  <option value={0}>Select property...</option>
                  {properties?.map((p) => (
                    <option key={p.id} value={p.id}>{p.code} - {p.name}</option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Unit Code *</label>
                  <Input
                    value={form.unit_code}
                    onChange={(e) => setForm({ ...form, unit_code: e.target.value })}
                    placeholder="e.g. 101"
                    required
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Type *</label>
                  <select
                    value={form.unit_type}
                    onChange={(e) => setForm({ ...form, unit_type: e.target.value as UnitType })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    {UNIT_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="text-sm font-medium">Floor</label>
                  <Input
                    value={form.floor}
                    onChange={(e) => setForm({ ...form, floor: e.target.value })}
                    placeholder="e.g. 1"
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Bedrooms</label>
                  <Input
                    type="number"
                    value={form.bedrooms}
                    onChange={(e) => setForm({ ...form, bedrooms: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Bathrooms</label>
                  <Input
                    type="number"
                    value={form.bathrooms}
                    onChange={(e) => setForm({ ...form, bathrooms: e.target.value })}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Area (sqm)</label>
                  <Input
                    type="number"
                    step="0.01"
                    value={form.area_sqm}
                    onChange={(e) => setForm({ ...form, area_sqm: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Default Rent</label>
                  <Input
                    type="number"
                    step="0.01"
                    value={form.default_rent}
                    onChange={(e) => setForm({ ...form, default_rent: e.target.value })}
                  />
                </div>
              </div>

              <div>
                <label className="text-sm font-medium">Notes</label>
                <textarea
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>

              <div className="flex gap-3 pt-2">
                <Button type="submit" disabled={createUnit.isPending}>
                  {createUnit.isPending ? "Creating..." : "Create Unit"}
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
