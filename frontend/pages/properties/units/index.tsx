import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, DoorOpen } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useUnits } from "@/queries/useProperties";
import { cn } from "@/lib/cn";

const STATUS_COLORS: Record<string, string> = {
  vacant: "bg-green-100 text-green-800",
  reserved: "bg-yellow-100 text-yellow-800",
  occupied: "bg-blue-100 text-blue-800",
  under_maintenance: "bg-orange-100 text-orange-800",
  inactive: "bg-gray-100 text-gray-800",
};

export default function UnitsPage() {
  const router = useRouter();
  const propertyFilter = router.query.property ? Number(router.query.property) : undefined;
  const { data: units, isLoading } = useUnits(
    propertyFilter ? { property: propertyFilter } : undefined
  );
  const [search, setSearch] = useState("");

  const filtered = units?.filter((u) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (
      u.unit_code.toLowerCase().includes(s) ||
      u.property_code.toLowerCase().includes(s) ||
      u.property_name.toLowerCase().includes(s)
    );
  });

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Units"
          subtitle="All units across properties"
          actions={
            <Link href="/properties/units/new">
              <Button>
                <Plus className="mr-2 h-4 w-4" />
                Add Unit
              </Button>
            </Link>
          }
        />

        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search units..."
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
            icon={<DoorOpen className="h-12 w-12" />}
            title="No units found"
            description="Add units to your properties."
            action={
              <Link href="/properties/units/new">
                <Button>
                  <Plus className="mr-2 h-4 w-4" />
                  Add Unit
                </Button>
              </Link>
            }
          />
        ) : (
          <div className="rounded-lg border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium">Unit Code</th>
                  <th className="px-4 py-3 text-left font-medium">Property</th>
                  <th className="px-4 py-3 text-left font-medium">Type</th>
                  <th className="px-4 py-3 text-left font-medium">Floor</th>
                  <th className="px-4 py-3 text-right font-medium">Default Rent</th>
                  <th className="px-4 py-3 text-center font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((unit) => (
                  <tr
                    key={unit.id}
                    className="border-b hover:bg-muted/30 cursor-pointer"
                    onClick={() => router.push(`/properties/units/${unit.id}`)}
                  >
                    <td className="px-4 py-3 font-medium">{unit.unit_code}</td>
                    <td className="px-4 py-3">
                      <span className="text-muted-foreground">{unit.property_code}</span>{" "}
                      {unit.property_name}
                    </td>
                    <td className="px-4 py-3 capitalize">{unit.unit_type.replace("_", " ")}</td>
                    <td className="px-4 py-3">{unit.floor || "—"}</td>
                    <td className="px-4 py-3 text-right">
                      {unit.default_rent ? Number(unit.default_rent).toLocaleString() : "—"}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <Badge className={cn("text-xs", STATUS_COLORS[unit.status])}>
                        {unit.status.replace("_", " ")}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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
