import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import {
  Activity,
  Search,
  Calendar,
  User,
  Building2,
  ChevronLeft,
  ChevronRight,
  Filter,
  Clock,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useQuery } from "@tanstack/react-query";
import { adminService, AuditEvent, AuditLogParams } from "@/services/admin.service";

export default function AdminAuditLogPage() {
  const { t } = useTranslation(["common"]);
  const { user } = useAuth();
  const router = useRouter();

  const [params, setParams] = useState<AuditLogParams>({
    limit: 50,
    offset: 0,
  });
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [showFilters, setShowFilters] = useState(false);

  // Redirect non-superusers
  useEffect(() => {
    if (user && !user.is_superuser) {
      router.replace("/dashboard");
    }
  }, [user, router]);

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "audit-log", params],
    queryFn: () => adminService.getAuditLog(params),
    enabled: user?.is_superuser,
  });

  const { data: eventTypesData } = useQuery({
    queryKey: ["admin", "event-types"],
    queryFn: () => adminService.getEventTypes(),
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

  const formatDateTime = (dateStr: string | null) => {
    if (!dateStr) return "-";
    return new Date(dateStr).toLocaleString();
  };

  const getEventTypeBadgeVariant = (eventType: string) => {
    if (eventType.includes("created")) return "default";
    if (eventType.includes("updated")) return "secondary";
    if (eventType.includes("deleted") || eventType.includes("voided")) return "destructive";
    if (eventType.includes("posted")) return "outline";
    return "secondary";
  };

  const getOriginBadge = (origin: string) => {
    switch (origin) {
      case "human":
        return <Badge variant="outline" className="gap-1"><User className="h-3 w-3" />Human</Badge>;
      case "batch":
        return <Badge variant="secondary" className="gap-1">Batch</Badge>;
      case "api":
        return <Badge variant="secondary" className="gap-1">API</Badge>;
      case "system":
        return <Badge variant="outline" className="gap-1">System</Badge>;
      default:
        return <Badge variant="secondary">{origin}</Badge>;
    }
  };

  const handleFilterChange = (eventType: string) => {
    setEventTypeFilter(eventType);
    setParams((prev) => ({
      ...prev,
      event_type: eventType || undefined,
      offset: 0,
    }));
  };

  const handlePrevPage = () => {
    setParams((prev) => ({
      ...prev,
      offset: Math.max(0, (prev.offset || 0) - (prev.limit || 50)),
    }));
  };

  const handleNextPage = () => {
    setParams((prev) => ({
      ...prev,
      offset: (prev.offset || 0) + (prev.limit || 50),
    }));
  };

  const totalPages = data ? Math.ceil(data.count / (params.limit || 50)) : 0;
  const currentPage = Math.floor((params.offset || 0) / (params.limit || 50)) + 1;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Audit Log"
          subtitle={`${data?.count || 0} business events recorded`}
        />

        {/* Filters */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap gap-4">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowFilters(!showFilters)}
                className="gap-2"
              >
                <Filter className="h-4 w-4" />
                Filters
              </Button>

              {showFilters && (
                <select
                  value={eventTypeFilter}
                  onChange={(e) => handleFilterChange(e.target.value)}
                  className="px-3 py-2 text-sm border rounded-md bg-background"
                >
                  <option value="">All Event Types</option>
                  {eventTypesData?.event_types.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              )}

              <div className="flex-1" />

              {/* Pagination */}
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">
                  Page {currentPage} of {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handlePrevPage}
                  disabled={(params.offset || 0) === 0}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleNextPage}
                  disabled={currentPage >= totalPages}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Events List */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5" />
              Events
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex justify-center py-8">
                <LoadingSpinner size="lg" />
              </div>
            ) : !data?.events?.length ? (
              <div className="text-center py-8 text-muted-foreground">
                No events found
              </div>
            ) : (
              <div className="space-y-3">
                {data.events.map((event) => (
                  <div
                    key={event.id}
                    className="border rounded-lg p-4 hover:bg-muted/50 transition-colors"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={getEventTypeBadgeVariant(event.event_type)}>
                            {event.event_type}
                          </Badge>
                          {getOriginBadge(event.origin)}
                        </div>
                        <div className="text-sm text-muted-foreground">
                          <span className="font-mono">
                            {event.aggregate_type}#{event.aggregate_id}
                          </span>
                        </div>
                      </div>
                      <div className="text-end text-sm text-muted-foreground">
                        <div className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {formatDateTime(event.occurred_at)}
                        </div>
                      </div>
                    </div>

                    <div className="mt-3 flex flex-wrap gap-4 text-sm">
                      {event.company_name && (
                        <div className="flex items-center gap-1 text-muted-foreground">
                          <Building2 className="h-4 w-4" />
                          {event.company_name}
                        </div>
                      )}
                      {event.caused_by_user_email && (
                        <div className="flex items-center gap-1 text-muted-foreground">
                          <User className="h-4 w-4" />
                          {event.caused_by_user_email}
                        </div>
                      )}
                    </div>

                    {event.data_preview && (
                      <div className="mt-3 p-2 bg-muted rounded text-xs font-mono text-muted-foreground overflow-hidden">
                        {event.data_preview}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Bottom Pagination */}
        {data && data.count > (params.limit || 50) && (
          <div className="flex justify-center gap-2">
            <Button
              variant="outline"
              onClick={handlePrevPage}
              disabled={(params.offset || 0) === 0}
            >
              <ChevronLeft className="h-4 w-4 mr-2" />
              Previous
            </Button>
            <Button
              variant="outline"
              onClick={handleNextPage}
              disabled={currentPage >= totalPages}
            >
              Next
              <ChevronRight className="h-4 w-4 ml-2" />
            </Button>
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
