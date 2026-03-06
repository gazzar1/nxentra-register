import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Building2, Pencil } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useProperties } from "@/queries/useProperties";
import type { Property, PropertyType } from "@/types/properties";
import { cn } from "@/lib/cn";

const PROPERTY_TYPE_LABELS: Record<PropertyType, string> = {
  residential_building: "Residential",
  apartment_block: "Apartment Block",
  villa: "Villa",
  office_building: "Office",
  warehouse: "Warehouse",
  retail: "Retail",
  land: "Land",
  mixed_use: "Mixed Use",
};

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-100 text-green-800",
  inactive: "bg-gray-100 text-gray-800",
};

export default function PropertiesPage() {
  const { t } = useTranslation(["common"]);
  const router = useRouter();
  const { data: properties, isLoading } = useProperties();
  const [search, setSearch] = useState("");

  const filtered = properties?.filter((p) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (
      p.code.toLowerCase().includes(s) ||
      p.name.toLowerCase().includes(s) ||
      p.city.toLowerCase().includes(s)
    );
  });

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Properties"
          subtitle="Manage your property portfolio"
          actions={
            <Link href="/properties/properties/new">
              <Button>
                <Plus className="mr-2 h-4 w-4" />
                Add Property
              </Button>
            </Link>
          }
        />

        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search properties..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
        </div>

        {isLoading ? (
          <LoadingSpinner />
        ) : !filtered?.length ? (
          <EmptyState
            icon={<Building2 className="h-12 w-12" />}
            title="No properties found"
            description="Create your first property to get started."
            action={
              <Link href="/properties/properties/new">
                <Button>
                  <Plus className="mr-2 h-4 w-4" />
                  Add Property
                </Button>
              </Link>
            }
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filtered.map((property) => (
              <Card
                key={property.id}
                className="cursor-pointer hover:shadow-md transition-shadow"
                onClick={() => router.push(`/properties/properties/${property.id}`)}
              >
                <CardContent className="p-5">
                  <div className="flex items-start justify-between">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-mono text-muted-foreground">
                          {property.code}
                        </span>
                        <Badge
                          className={cn("text-xs", STATUS_COLORS[property.status])}
                        >
                          {property.status}
                        </Badge>
                      </div>
                      <h3 className="font-semibold">{property.name}</h3>
                      <p className="text-sm text-muted-foreground">
                        {PROPERTY_TYPE_LABELS[property.property_type]}
                      </p>
                    </div>
                    <Link
                      href={`/properties/properties/${property.id}`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Button variant="ghost" size="icon">
                        <Pencil className="h-4 w-4" />
                      </Button>
                    </Link>
                  </div>
                  <div className="mt-3 flex items-center gap-4 text-sm text-muted-foreground">
                    {property.city && <span>{property.city}</span>}
                    <span>{property.unit_count} units</span>
                    {property.area_sqm && (
                      <span>{Number(property.area_sqm).toLocaleString()} sqm</span>
                    )}
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
