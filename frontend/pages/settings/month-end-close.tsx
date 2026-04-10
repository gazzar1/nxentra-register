import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2, AlertTriangle, XCircle, ClipboardCheck,
  ChevronLeft, ChevronRight, Lock, RefreshCw, ArrowRight,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { reportsService, MonthEndCheck } from "@/services/reports.service";
import apiClient from "@/lib/api-client";
import { cn } from "@/lib/cn";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const STATUS_ICON = {
  PASS: CheckCircle2,
  WARN: AlertTriangle,
  FAIL: XCircle,
};

const STATUS_COLOR = {
  PASS: "text-green-600",
  WARN: "text-yellow-600",
  FAIL: "text-red-600",
};

const STATUS_BG = {
  PASS: "bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-800",
  WARN: "bg-yellow-50 dark:bg-yellow-950/30 border-yellow-200 dark:border-yellow-800",
  FAIL: "bg-red-50 dark:bg-red-950/30 border-red-200 dark:border-red-800",
};

const STATUS_BADGE = {
  PASS: "bg-green-100 text-green-800",
  WARN: "bg-yellow-100 text-yellow-800",
  FAIL: "bg-red-100 text-red-800",
};

export default function MonthEndClosePage() {
  const { t } = useTranslation(["common"]);
  const router = useRouter();
  const { toast } = useToast();

  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth()); // Previous month (0-indexed, so getMonth() = previous)
  const [showCloseDialog, setShowCloseDialog] = useState(false);
  const [closing, setClosing] = useState(false);

  // Adjust: if we're at month 0 (Jan), previous month is Dec of last year
  const displayMonth = month + 1; // 1-indexed for API

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["month-end-close", year, displayMonth],
    queryFn: async () => {
      const { data } = await reportsService.monthEndClose({ year, month: displayMonth });
      return data;
    },
  });

  const handlePrevMonth = () => {
    if (month === 0) {
      setMonth(11);
      setYear(year - 1);
    } else {
      setMonth(month - 1);
    }
  };

  const handleNextMonth = () => {
    if (month === 11) {
      setMonth(0);
      setYear(year + 1);
    } else {
      setMonth(month + 1);
    }
  };

  const handleClosePeriod = async () => {
    if (!data?.fiscal_period) return;
    setClosing(true);
    try {
      await apiClient.post(`/reports/periods/${data.fiscal_period.fiscal_year}/${data.fiscal_period.period}/close/`);
      toast({ title: "Period closed", description: `Period ${displayMonth}/${year} has been closed successfully.` });
      refetch();
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || error?.response?.data?.error || "Failed to close period.", variant: "destructive" });
    } finally {
      setClosing(false);
      setShowCloseDialog(false);
    }
  };

  const periodLabel = `${MONTHS[month]} ${year}`;
  const isPeriodOpen = data?.fiscal_period?.status === "OPEN";

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Month-End Close"
          subtitle="Guided period close with pre-flight validation"
          actions={
            <Button variant="outline" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={cn("h-4 w-4 me-2", isFetching && "animate-spin")} />
              Re-check
            </Button>
          }
        />

        {/* Period Selector */}
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between">
              <Button variant="ghost" size="sm" onClick={handlePrevMonth}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <div className="text-center">
                <h2 className="text-xl font-bold">{periodLabel}</h2>
                {data?.fiscal_period ? (
                  <Badge className={isPeriodOpen ? STATUS_BADGE.WARN : STATUS_BADGE.PASS}>
                    {isPeriodOpen ? "Open" : "Closed"}
                  </Badge>
                ) : (
                  <Badge className="bg-gray-100 text-gray-800">No fiscal period</Badge>
                )}
              </div>
              <Button variant="ghost" size="sm" onClick={handleNextMonth}>
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>

        {isLoading ? (
          <div className="flex justify-center py-12"><LoadingSpinner /></div>
        ) : data ? (
          <>
            {/* Summary */}
            <Card className={cn("border", data.ready_to_close
              ? "bg-green-50 dark:bg-green-950/30 border-green-200"
              : "bg-red-50 dark:bg-red-950/30 border-red-200"
            )}>
              <CardContent className="py-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <ClipboardCheck className={cn("h-6 w-6", data.ready_to_close ? "text-green-600" : "text-red-600")} />
                    <div>
                      <p className={cn("text-lg font-semibold", data.ready_to_close ? "text-green-700 dark:text-green-300" : "text-red-700 dark:text-red-300")}>
                        {data.ready_to_close ? "Ready to Close" : "Not Ready — Resolve Issues Below"}
                      </p>
                      <p className="text-sm text-muted-foreground">
                        {data.passed} passed, {data.warned} warnings, {data.failed} failures
                      </p>
                    </div>
                  </div>
                  {data.ready_to_close && isPeriodOpen && (
                    <Button onClick={() => setShowCloseDialog(true)} disabled={closing}>
                      <Lock className="h-4 w-4 me-2" />
                      Close Period
                    </Button>
                  )}
                  {!isPeriodOpen && data.fiscal_period && (
                    <Badge className={STATUS_BADGE.PASS}>Already Closed</Badge>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Checks */}
            <Card>
              <CardHeader>
                <CardTitle>Pre-Close Checklist</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {data.checks.map((check: MonthEndCheck, idx: number) => {
                  const Icon = STATUS_ICON[check.status];
                  return (
                    <div key={check.check} className={cn("rounded-lg border p-4", STATUS_BG[check.status])}>
                      <div className="flex items-start gap-3">
                        <div className="flex items-center gap-2 mt-0.5 shrink-0">
                          <span className="text-xs text-muted-foreground font-mono w-4">{idx + 1}</span>
                          <Icon className={cn("h-5 w-5", STATUS_COLOR[check.status])} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-0.5">
                            <span className="font-medium">{check.title}</span>
                            <Badge className={cn("text-xs", STATUS_BADGE[check.status])}>{check.status}</Badge>
                          </div>
                          <p className="text-sm text-muted-foreground">{check.message}</p>
                          {check.resolution && (
                            <div className="flex items-start gap-1.5 mt-2 text-sm text-primary">
                              <ArrowRight className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                              <span>{check.resolution}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          </>
        ) : null}
      </div>

      {/* Close Period Confirmation */}
      <AlertDialog open={showCloseDialog} onOpenChange={setShowCloseDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Close Period: {periodLabel}</AlertDialogTitle>
            <AlertDialogDescription>
              This will lock the period and prevent any new journal entries from being posted to {periodLabel}.
              All {data?.passed || 0} checks passed. Are you sure you want to close this period?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleClosePeriod} disabled={closing}>
              {closing ? "Closing..." : "Close Period"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common"])) } };
};
