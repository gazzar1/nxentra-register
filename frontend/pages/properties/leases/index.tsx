import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, FileText } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useLeases } from "@/queries/useProperties";
import { cn } from "@/lib/cn";

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  active: "bg-green-100 text-green-800",
  expired: "bg-yellow-100 text-yellow-800",
  terminated: "bg-red-100 text-red-800",
  renewed: "bg-blue-100 text-blue-800",
};

export default function LeasesPage() {
  const router = useRouter();
  const { data: leases, isLoading } = useLeases();
  const [search, setSearch] = useState("");

  const filtered = leases?.filter((l) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (
      l.contract_no.toLowerCase().includes(s) ||
      l.property_code.toLowerCase().includes(s) ||
      l.lessee_name.toLowerCase().includes(s)
    );
  });

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Leases"
          subtitle="Manage lease contracts"
          actions={
            <Link href="/properties/leases/new">
              <Button>
                <Plus className="mr-2 h-4 w-4" />
                New Lease
              </Button>
            </Link>
          }
        />

        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search leases..."
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
            icon={<FileText className="h-12 w-12" />}
            title="No leases found"
            description="Create your first lease contract."
            action={
              <Link href="/properties/leases/new">
                <Button>
                  <Plus className="mr-2 h-4 w-4" />
                  New Lease
                </Button>
              </Link>
            }
          />
        ) : (
          <div className="rounded-lg border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium">Contract #</th>
                  <th className="px-4 py-3 text-left font-medium">Property</th>
                  <th className="px-4 py-3 text-left font-medium">Unit</th>
                  <th className="px-4 py-3 text-left font-medium">Lessee</th>
                  <th className="px-4 py-3 text-left font-medium">Period</th>
                  <th className="px-4 py-3 text-right font-medium">Rent</th>
                  <th className="px-4 py-3 text-center font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((lease) => (
                  <tr
                    key={lease.id}
                    className="border-b hover:bg-muted/30 cursor-pointer"
                    onClick={() => router.push(`/properties/leases/${lease.id}`)}
                  >
                    <td className="px-4 py-3 font-medium">{lease.contract_no}</td>
                    <td className="px-4 py-3">{lease.property_code} - {lease.property_name}</td>
                    <td className="px-4 py-3">{lease.unit_code || "Whole property"}</td>
                    <td className="px-4 py-3">{lease.lessee_name}</td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {lease.start_date} — {lease.end_date}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {Number(lease.rent_amount).toLocaleString()} {lease.currency}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <Badge className={cn("text-xs", STATUS_COLORS[lease.status])}>
                        {lease.status}
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
