import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toaster";
import { useProperty, useUpdateProperty, useUnits } from "@/queries/useProperties";
import type { PropertyType, PropertyStatus } from "@/types/properties";
import { useState, useEffect } from "react";
import { cn } from "@/lib/cn";
import Link from "next/link";
import { Plus } from "lucide-react";

const PROPERTY_TYPES: { value: PropertyType; label: string }[] = [
  { value: "residential_building", label: "Residential Building" },
  { value: "apartment_block", label: "Apartment Block" },
  { value: "villa", label: "Villa" },
  { value: "office_building", label: "Office Building" },
  { value: "warehouse", label: "Warehouse" },
  { value: "retail", label: "Retail" },
  { value: "land", label: "Land" },
  { value: "mixed_use", label: "Mixed Use" },
];

const UNIT_STATUS_COLORS: Record<string, string> = {
  vacant: "bg-green-100 text-green-800",
  reserved: "bg-yellow-100 text-yellow-800",
  occupied: "bg-blue-100 text-blue-800",
  under_maintenance: "bg-orange-100 text-orange-800",
  inactive: "bg-gray-100 text-gray-800",
};

export default function PropertyDetailPage() {
  const router = useRouter();
  const { toast } = useToast();
  const id = Number(router.query.id);
  const { data: property, isLoading } = useProperty(id);
  const { data: units } = useUnits({ property: id });
  const updateProperty = useUpdateProperty();

  const [form, setForm] = useState({
    name: "",
    name_ar: "",
    property_type: "residential_building" as PropertyType,
    status: "active" as PropertyStatus,
    address: "",
    city: "",
    region: "",
    country: "SA",
    notes: "",
  });

  useEffect(() => {
    if (property) {
      setForm({
        name: property.name,
        name_ar: property.name_ar,
        property_type: property.property_type,
        status: property.status,
        address: property.address,
        city: property.city,
        region: property.region,
        country: property.country,
        notes: property.notes,
      });
    }
  }, [property]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await updateProperty.mutateAsync({ id, ...form });
      toast({ title: "Property updated" });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to update property.",
        variant: "destructive",
      });
    }
  };

  if (isLoading) return <AppLayout><LoadingSpinner /></AppLayout>;
  if (!property) return <AppLayout><div>Property not found</div></AppLayout>;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={`${property.code} - ${property.name}`}
          subtitle="Property details and units"
        />

        <div className="grid gap-6 lg:grid-cols-3">
          {/* Edit form */}
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Property Details</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-sm font-medium">Type</label>
                    <select
                      value={form.property_type}
                      onChange={(e) => setForm({ ...form, property_type: e.target.value as PropertyType })}
                      className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    >
                      {PROPERTY_TYPES.map((t) => (
                        <option key={t.value} value={t.value}>{t.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-sm font-medium">Status</label>
                    <select
                      value={form.status}
                      onChange={(e) => setForm({ ...form, status: e.target.value as PropertyStatus })}
                      className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    >
                      <option value="active">Active</option>
                      <option value="inactive">Inactive</option>
                    </select>
                  </div>
                </div>

                <div>
                  <label className="text-sm font-medium">Name</label>
                  <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </div>

                <div>
                  <label className="text-sm font-medium">Name (Arabic)</label>
                  <Input value={form.name_ar} onChange={(e) => setForm({ ...form, name_ar: e.target.value })} dir="rtl" />
                </div>

                <div>
                  <label className="text-sm font-medium">Address</label>
                  <Input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
                </div>

                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="text-sm font-medium">City</label>
                    <Input value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-sm font-medium">Region</label>
                    <Input value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-sm font-medium">Country</label>
                    <Input value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} />
                  </div>
                </div>

                <div>
                  <label className="text-sm font-medium">Notes</label>
                  <textarea
                    value={form.notes}
                    onChange={(e) => setForm({ ...form, notes: e.target.value })}
                    className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  />
                </div>

                <Button type="submit" disabled={updateProperty.isPending}>
                  {updateProperty.isPending ? "Saving..." : "Save Changes"}
                </Button>
              </form>
            </CardContent>
          </Card>

          {/* Units list */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Units ({units?.length || 0})</CardTitle>
              <Link href={`/properties/units/new?property=${id}`}>
                <Button size="sm" variant="outline">
                  <Plus className="mr-1 h-3 w-3" />
                  Add
                </Button>
              </Link>
            </CardHeader>
            <CardContent>
              {!units?.length ? (
                <p className="text-sm text-muted-foreground">No units yet.</p>
              ) : (
                <div className="space-y-2">
                  {units.map((unit) => (
                    <Link
                      key={unit.id}
                      href={`/properties/units/${unit.id}`}
                      className="flex items-center justify-between rounded-lg border p-3 hover:bg-muted transition-colors"
                    >
                      <div>
                        <span className="font-medium">{unit.unit_code}</span>
                        <span className="ml-2 text-sm text-muted-foreground capitalize">
                          {unit.unit_type.replace("_", " ")}
                        </span>
                      </div>
                      <Badge className={cn("text-xs", UNIT_STATUS_COLORS[unit.status])}>
                        {unit.status.replace("_", " ")}
                      </Badge>
                    </Link>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
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
