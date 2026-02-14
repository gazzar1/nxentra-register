import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { Building2, Search, Users, Calendar, Globe } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useQuery } from "@tanstack/react-query";
import { adminService, AdminCompany } from "@/services/admin.service";

export default function AdminCompaniesPage() {
  const { t } = useTranslation(["common"]);
  const { user } = useAuth();
  const router = useRouter();
  const [searchQuery, setSearchQuery] = useState("");

  // Redirect non-superusers
  useEffect(() => {
    if (user && !user.is_superuser) {
      router.replace("/dashboard");
    }
  }, [user, router]);

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "companies"],
    queryFn: () => adminService.getCompanies(),
    enabled: user?.is_superuser,
  });

  if (!user?.is_superuser) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center h-64">
          <p className="text-muted-foreground">Access denied. Superuser only.</p>
        </div>
      </AppLayout>
    );
  }

  const filteredCompanies = data?.companies?.filter(
    (company) =>
      company.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      company.owner_email?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      company.slug.toLowerCase().includes(searchQuery.toLowerCase())
  ) || [];

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "-";
    return new Date(dateStr).toLocaleDateString();
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="All Companies"
          subtitle={`${data?.count || 0} companies registered in the system`}
        />

        {/* Search */}
        <Card>
          <CardContent className="pt-6">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search companies by name, owner, or slug..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-10"
              />
            </div>
          </CardContent>
        </Card>

        {/* Companies Table */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Building2 className="h-5 w-5" />
              Companies ({filteredCompanies.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex justify-center py-8">
                <LoadingSpinner size="lg" />
              </div>
            ) : filteredCompanies.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                No companies found
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b">
                      <th className="text-start py-3 px-2 font-medium">Company</th>
                      <th className="text-start py-3 px-2 font-medium">Owner</th>
                      <th className="text-start py-3 px-2 font-medium">Currency</th>
                      <th className="text-center py-3 px-2 font-medium">Members</th>
                      <th className="text-center py-3 px-2 font-medium">Status</th>
                      <th className="text-start py-3 px-2 font-medium">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredCompanies.map((company) => (
                      <tr key={company.id} className="border-b hover:bg-muted/50">
                        <td className="py-3 px-2">
                          <div>
                            <div className="font-medium">{company.name}</div>
                            {company.name_ar && (
                              <div className="text-sm text-muted-foreground">
                                {company.name_ar}
                              </div>
                            )}
                            <div className="text-xs text-muted-foreground ltr-code">
                              {company.slug}
                            </div>
                          </div>
                        </td>
                        <td className="py-3 px-2">
                          <div>
                            <div className="text-sm">{company.owner_name || "-"}</div>
                            <div className="text-xs text-muted-foreground">
                              {company.owner_email || "-"}
                            </div>
                          </div>
                        </td>
                        <td className="py-3 px-2">
                          <Badge variant="outline" className="gap-1">
                            <Globe className="h-3 w-3" />
                            {company.default_currency}
                          </Badge>
                        </td>
                        <td className="py-3 px-2 text-center">
                          <Badge variant="secondary" className="gap-1">
                            <Users className="h-3 w-3" />
                            {company.member_count}
                          </Badge>
                        </td>
                        <td className="py-3 px-2 text-center">
                          <Badge
                            variant={company.is_active ? "default" : "destructive"}
                          >
                            {company.is_active ? "Active" : "Inactive"}
                          </Badge>
                        </td>
                        <td className="py-3 px-2">
                          <div className="flex items-center gap-1 text-sm text-muted-foreground">
                            <Calendar className="h-3 w-3" />
                            {formatDate(company.created_at)}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
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
