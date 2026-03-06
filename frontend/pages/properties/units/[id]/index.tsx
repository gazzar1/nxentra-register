import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/toaster";
import { useUnit, useUpdateUnit } from "@/queries/useProperties";
import type { UnitType, UnitStatus } from "@/types/properties";
import { useState, useEffect } from "react";

const UNIT_TYPES: { value: UnitType; label: string }[] = [
  { value: "apartment", label: "Apartment" },
  { value: "office", label: "Office" },
  { value: "shop", label: "Shop" },
  { value: "warehouse_bay", label: "Warehouse Bay" },
  { value: "room", label: "Room" },
  { value: "parking", label: "Parking" },
  { value: "other", label: "Other" },
];

export default function UnitDetailPage() {
  const router = useRouter();
  const { toast } = useToast();
  const id = Number(router.query.id);
  const { data: unit, isLoading } = useUnit(id);
  const updateUnit = useUpdateUnit();

  const [form, setForm] = useState({
    unit_type: "apartment" as UnitType,
    status: "vacant" as UnitStatus,
    floor: "",
    bedrooms: "" as string,
    bathrooms: "" as string,
    area_sqm: "" as string,
    default_rent: "" as string,
    notes: "",
  });

  useEffect(() => {
    if (unit) {
      setForm({
        unit_type: unit.unit_type,
        status: unit.status,
        floor: unit.floor || "",
        bedrooms: unit.bedrooms?.toString() || "",
        bathrooms: unit.bathrooms?.toString() || "",
        area_sqm: unit.area_sqm || "",
        default_rent: unit.default_rent || "",
        notes: unit.notes,
      });
    }
  }, [unit]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await updateUnit.mutateAsync({
        id,
        unit_type: form.unit_type,
        status: form.status,
        floor: form.floor || null,
        bedrooms: form.bedrooms ? Number(form.bedrooms) : null,
        bathrooms: form.bathrooms ? Number(form.bathrooms) : null,
        area_sqm: form.area_sqm ? Number(form.area_sqm) : null,
        default_rent: form.default_rent ? Number(form.default_rent) : null,
        notes: form.notes,
      });
      toast({ title: "Unit updated" });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to update unit.",
        variant: "destructive",
      });
    }
  };

  if (isLoading) return <AppLayout><LoadingSpinner /></AppLayout>;
  if (!unit) return <AppLayout><div>Unit not found</div></AppLayout>;

  return (
    <AppLayout>
      <div className="max-w-2xl space-y-6">
        <PageHeader
          title={`Unit ${unit.unit_code}`}
          subtitle={`${unit.property_code} - ${unit.property_name}`}
        />

        <Card>
          <CardHeader>
            <CardTitle>Unit Details</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Type</label>
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
                <div>
                  <label className="text-sm font-medium">Status</label>
                  <select
                    value={form.status}
                    onChange={(e) => setForm({ ...form, status: e.target.value as UnitStatus })}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="vacant">Vacant</option>
                    <option value="reserved">Reserved</option>
                    <option value="occupied">Occupied</option>
                    <option value="under_maintenance">Under Maintenance</option>
                    <option value="inactive">Inactive</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="text-sm font-medium">Floor</label>
                  <Input value={form.floor} onChange={(e) => setForm({ ...form, floor: e.target.value })} />
                </div>
                <div>
                  <label className="text-sm font-medium">Bedrooms</label>
                  <Input type="number" value={form.bedrooms} onChange={(e) => setForm({ ...form, bedrooms: e.target.value })} />
                </div>
                <div>
                  <label className="text-sm font-medium">Bathrooms</label>
                  <Input type="number" value={form.bathrooms} onChange={(e) => setForm({ ...form, bathrooms: e.target.value })} />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium">Area (sqm)</label>
                  <Input type="number" step="0.01" value={form.area_sqm} onChange={(e) => setForm({ ...form, area_sqm: e.target.value })} />
                </div>
                <div>
                  <label className="text-sm font-medium">Default Rent</label>
                  <Input type="number" step="0.01" value={form.default_rent} onChange={(e) => setForm({ ...form, default_rent: e.target.value })} />
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

              <Button type="submit" disabled={updateUnit.isPending}>
                {updateUnit.isPending ? "Saving..." : "Save Changes"}
              </Button>
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
