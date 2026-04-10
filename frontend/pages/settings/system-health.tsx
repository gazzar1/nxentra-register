import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, CheckCircle2, AlertTriangle, XCircle, Activity, ArrowRight } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { reportsService, SystemHealthCheck } from "@/services/reports.service";
import { cn } from "@/lib/cn";

const STATUS_CONFIG = {
  PASS: { icon: CheckCircle2, color: "text-green-600", bg: "bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-800", badge: "bg-green-100 text-green-800" },
  WARN: { icon: AlertTriangle, color: "text-yellow-600", bg: "bg-yellow-50 dark:bg-yellow-950/30 border-yellow-200 dark:border-yellow-800", badge: "bg-yellow-100 text-yellow-800" },
  FAIL: { icon: XCircle, color: "text-red-600", bg: "bg-red-50 dark:bg-red-950/30 border-red-200 dark:border-red-800", badge: "bg-red-100 text-red-800" },
};

const OVERALL_CONFIG = {
  healthy: { label: "All Systems Healthy", color: "text-green-600", bg: "bg-green-50 dark:bg-green-950/30" },
  attention: { label: "Needs Attention", color: "text-yellow-600", bg: "bg-yellow-50 dark:bg-yellow-950/30" },
  unhealthy: { label: "Issues Detected", color: "text-red-600", bg: "bg-red-50 dark:bg-red-950/30" },
};

export default function SystemHealthPage() {
  const { t } = useTranslation(["common"]);

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["system-health"],
    queryFn: async () => {
      const { data } = await reportsService.systemHealth();
      return data;
    },
    refetchInterval: 60000,
  });

  const summary = data?.summary;
  const overall = summary ? OVERALL_CONFIG[summary.overall] : null;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="System Health"
          subtitle="Real-time diagnostics for your accounting system"
          actions={
            <Button variant="outline" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={cn("h-4 w-4 me-2", isFetching && "animate-spin")} />
              Refresh
            </Button>
          }
        />

        {isLoading ? (
          <div className="flex justify-center py-12"><LoadingSpinner /></div>
        ) : data ? (
          <>
            {/* Overall Status Banner */}
            {overall && (
              <Card className={cn("border", overall.bg)}>
                <CardContent className="py-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <Activity className={cn("h-6 w-6", overall.color)} />
                      <div>
                        <p className={cn("text-lg font-semibold", overall.color)}>{overall.label}</p>
                        <p className="text-sm text-muted-foreground">
                          {summary!.passed} passed, {summary!.warned} warnings, {summary!.failed} failures
                        </p>
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <Badge className={STATUS_CONFIG.PASS.badge}>{summary!.passed} Pass</Badge>
                      {summary!.warned > 0 && <Badge className={STATUS_CONFIG.WARN.badge}>{summary!.warned} Warn</Badge>}
                      {summary!.failed > 0 && <Badge className={STATUS_CONFIG.FAIL.badge}>{summary!.failed} Fail</Badge>}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Individual Checks */}
            <div className="grid gap-4">
              {data.checks.map((check: SystemHealthCheck) => {
                const config = STATUS_CONFIG[check.status];
                const Icon = config.icon;
                return (
                  <Card key={check.check} className={cn("border", config.bg)}>
                    <CardContent className="py-4">
                      <div className="flex items-start gap-4">
                        <Icon className={cn("h-5 w-5 mt-0.5 shrink-0", config.color)} />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <h3 className="font-medium">{check.title}</h3>
                            <Badge className={cn("text-xs", config.badge)}>{check.status}</Badge>
                          </div>
                          <p className="text-sm text-muted-foreground">{check.message}</p>
                          {check.action && (
                            <div className="flex items-center gap-1 mt-2 text-sm font-medium text-primary">
                              <ArrowRight className="h-3.5 w-3.5" />
                              {check.action}
                            </div>
                          )}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          </>
        ) : null}
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common"])) } };
};
