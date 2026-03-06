import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Users, Pencil } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useLessees } from "@/queries/useProperties";
import { cn } from "@/lib/cn";

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-100 text-green-800",
  inactive: "bg-gray-100 text-gray-800",
  blacklisted: "bg-red-100 text-red-800",
};

export default function LesseesPage() {
  const router = useRouter();
  const { data: lessees, isLoading } = useLessees();
  const [search, setSearch] = useState("");

  const filtered = lessees?.filter((l) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (
      l.code.toLowerCase().includes(s) ||
      l.display_name.toLowerCase().includes(s) ||
      l.email?.toLowerCase().includes(s) ||
      l.phone?.includes(s)
    );
  });

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Lessees"
          subtitle="Manage property tenants"
          actions={
            <Link href="/properties/lessees/new">
              <Button>
                <Plus className="mr-2 h-4 w-4" />
                Add Lessee
              </Button>
            </Link>
          }
        />

        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search lessees..."
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
            icon={<Users className="h-12 w-12" />}
            title="No lessees found"
            description="Add your first lessee to get started."
            action={
              <Link href="/properties/lessees/new">
                <Button>
                  <Plus className="mr-2 h-4 w-4" />
                  Add Lessee
                </Button>
              </Link>
            }
          />
        ) : (
          <div className="rounded-lg border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium">Code</th>
                  <th className="px-4 py-3 text-left font-medium">Name</th>
                  <th className="px-4 py-3 text-left font-medium">Type</th>
                  <th className="px-4 py-3 text-left font-medium">Phone</th>
                  <th className="px-4 py-3 text-left font-medium">Email</th>
                  <th className="px-4 py-3 text-center font-medium">Status</th>
                  <th className="px-4 py-3 text-center font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((lessee) => (
                  <tr
                    key={lessee.id}
                    className="border-b hover:bg-muted/30 cursor-pointer"
                    onClick={() => router.push(`/properties/lessees/${lessee.id}`)}
                  >
                    <td className="px-4 py-3 font-mono text-muted-foreground">{lessee.code}</td>
                    <td className="px-4 py-3 font-medium">{lessee.display_name}</td>
                    <td className="px-4 py-3 capitalize">{lessee.lessee_type}</td>
                    <td className="px-4 py-3">{lessee.phone || "—"}</td>
                    <td className="px-4 py-3">{lessee.email || "—"}</td>
                    <td className="px-4 py-3 text-center">
                      <Badge className={cn("text-xs", STATUS_COLORS[lessee.status])}>
                        {lessee.status}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-center">
                      <Link
                        href={`/properties/lessees/${lessee.id}`}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Button variant="ghost" size="icon">
                          <Pencil className="h-4 w-4" />
                        </Button>
                      </Link>
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
