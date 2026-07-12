import { useEffect, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, AlertTriangle, XCircle, ClipboardCheck,
  ChevronLeft, ChevronRight, Lock, RefreshCw, ArrowRight, BookOpen, ChevronDown,
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
import { Textarea } from "@/components/ui/textarea";
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
  const [showForceDialog, setShowForceDialog] = useState(false);
  const [forceReason, setForceReason] = useState("");
  // A152: blocking failures returned by a 400 on close — the server's word
  // beats the page-load snapshot (a draft can land between GET and click).
  const [serverBlocking, setServerBlocking] = useState<MonthEndCheck[] | null>(null);
  const [closing, setClosing] = useState(false);
  const [showGuide, setShowGuide] = useState(false);
  const qc = useQueryClient();

  // A152 item 6: the header chip deep-links ?year=&month= so this page opens
  // on the period the chip named instead of the previous-calendar-month default.
  useEffect(() => {
    if (!router.isReady) return;
    const qy = Number(router.query.year);
    const qm = Number(router.query.month);
    if (qy >= 2000 && qy <= 2100 && qm >= 1 && qm <= 12) {
      setYear(qy);
      setMonth(qm - 1);
    }
  }, [router.isReady]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const handleClosePeriod = async (opts?: { force?: boolean; reason?: string }) => {
    if (!data?.fiscal_period) return;
    setClosing(true);
    try {
      await apiClient.post(
        `/reports/periods/${data.fiscal_period.fiscal_year}/${data.fiscal_period.period}/close/`,
        opts ? { force: !!opts.force, reason: opts.reason } : undefined
      );
      toast({ title: "Period closed", description: `Period ${displayMonth}/${year} has been closed successfully.` });
      refetch();
      // Refresh the shared fiscal-periods cache so the header chip and the
      // CompanyDateInput warnings reflect the close immediately.
      qc.invalidateQueries({ queryKey: ["fiscal-periods"] });
      // Close dialogs only on SUCCESS — a failed force keeps the typed reason.
      setShowCloseDialog(false);
      setShowForceDialog(false);
      setServerBlocking(null);
    } catch (error: any) {
      const resp = error?.response;
      if (!opts?.force && resp?.status === 400 && Array.isArray(resp.data?.checklist)) {
        // The server gate blocked a plain close (checklist went stale after
        // page load) — swap to the force dialog seeded from the SERVER's
        // checklist, mirroring periods.tsx, and refresh the on-page list.
        setShowCloseDialog(false);
        setServerBlocking(
          (resp.data.checklist as MonthEndCheck[]).filter((c) => c.status === "FAIL" && c.blocking)
        );
        setForceReason("");
        setShowForceDialog(true);
        refetch();
      } else {
        toast({ title: "Error", description: resp?.data?.detail || resp?.data?.error || "Failed to close period.", variant: "destructive" });
      }
    } finally {
      setClosing(false);
    }
  };

  const periodLabel = `${MONTHS[month]} ${year}`;
  const isPeriodOpen = data?.fiscal_period?.status === "OPEN";
  // A152 item 3: only a FAIL on a BLOCKING check (trial balance, drafts) stops
  // a close. Non-blocking FAILs (e.g. Shopify store) and WARNs are advisory —
  // the operator can close, or force past a blocking failure with a reason.
  // Server-reported failures (from a blocked POST) supersede the GET snapshot.
  const blockingFailures =
    serverBlocking ?? (data?.checks ?? []).filter((c) => c.status === "FAIL" && c.blocking);
  const hasBlocking = blockingFailures.length > 0;

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

        {/* How-To Guide */}
        <Card className="border-blue-200 dark:border-blue-900">
          <button
            onClick={() => setShowGuide(!showGuide)}
            className="w-full px-6 py-3 flex items-center justify-between text-start"
          >
            <div className="flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-blue-600" />
              <span className="font-medium text-sm">How to close a period (step-by-step guide)</span>
            </div>
            <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform", showGuide && "rotate-180")} />
          </button>
          {showGuide && (
            <CardContent className="pt-0 pb-4">
              <ol className="space-y-3 text-sm text-muted-foreground">
                <li className="flex gap-3">
                  <span className="font-mono text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full h-5 w-5 flex items-center justify-center shrink-0 mt-0.5">1</span>
                  <div><span className="font-medium text-foreground">Post all entries.</span> Go to Journal Entries, filter by Draft/Incomplete. Post or delete each one. No unfinished entries should remain in the period.</div>
                </li>
                <li className="flex gap-3">
                  <span className="font-mono text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full h-5 w-5 flex items-center justify-center shrink-0 mt-0.5">2</span>
                  <div><span className="font-medium text-foreground">Sync Shopify data.</span> Go to Shopify &gt; Settings and click &quot;Sync Payouts&quot; and &quot;Re-sync Orders&quot; to catch any missed webhooks.</div>
                </li>
                <li className="flex gap-3">
                  <span className="font-mono text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full h-5 w-5 flex items-center justify-center shrink-0 mt-0.5">3</span>
                  <div><span className="font-medium text-foreground">Review reconciliation.</span> Go to Finance &gt; Reconciliation and clear the exception queue. For Shopify payout-level Verify, the Shopify Reconciliation page pulls transaction details per payout.</div>
                </li>
                <li className="flex gap-3">
                  <span className="font-mono text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full h-5 w-5 flex items-center justify-center shrink-0 mt-0.5">4</span>
                  <div><span className="font-medium text-foreground">Check the clearing balance.</span> A non-zero clearing balance is normal if orders are awaiting payout. It should be explainable by unsettled orders.</div>
                </li>
                <li className="flex gap-3">
                  <span className="font-mono text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full h-5 w-5 flex items-center justify-center shrink-0 mt-0.5">5</span>
                  <div><span className="font-medium text-foreground">Run currency revaluation</span> (if you have foreign currency transactions). Go to Currency Revaluation, select the period end date, and post.</div>
                </li>
                <li className="flex gap-3">
                  <span className="font-mono text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full h-5 w-5 flex items-center justify-center shrink-0 mt-0.5">6</span>
                  <div><span className="font-medium text-foreground">Run the pre-close checks below.</span> All items should show Pass. Fix any failures using the suggested resolution steps.</div>
                </li>
                <li className="flex gap-3">
                  <span className="font-mono text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full h-5 w-5 flex items-center justify-center shrink-0 mt-0.5">7</span>
                  <div><span className="font-medium text-foreground">Close the period.</span> Click the &quot;Close Period&quot; button. This locks the period and prevents any new entries from being posted to it.</div>
                </li>
              </ol>
            </CardContent>
          )}
        </Card>

        {isLoading ? (
          <div className="flex justify-center py-12"><LoadingSpinner /></div>
        ) : data ? (
          <>
            {/* Summary */}
            <Card className={cn("border", !hasBlocking
              ? "bg-green-50 dark:bg-green-950/30 border-green-200"
              : "bg-red-50 dark:bg-red-950/30 border-red-200"
            )}>
              <CardContent className="py-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <ClipboardCheck className={cn("h-6 w-6", !hasBlocking ? "text-green-600" : "text-red-600")} />
                    <div>
                      <p className={cn("text-lg font-semibold", !hasBlocking ? "text-green-700 dark:text-green-300" : "text-red-700 dark:text-red-300")}>
                        {hasBlocking
                          ? "Blocking issues — resolve or force close"
                          : data.failed > 0
                            ? "Ready to close (advisory issues remain)"
                            : "Ready to Close"}
                      </p>
                      <p className="text-sm text-muted-foreground">
                        {data.passed} passed, {data.warned} warnings, {data.failed} failures
                      </p>
                    </div>
                  </div>
                  {isPeriodOpen && !hasBlocking && (
                    <Button onClick={() => setShowCloseDialog(true)} disabled={closing}>
                      <Lock className="h-4 w-4 me-2" />
                      Close Period
                    </Button>
                  )}
                  {isPeriodOpen && hasBlocking && (
                    <Button
                      variant="destructive"
                      onClick={() => {
                        setForceReason("");
                        setShowForceDialog(true);
                      }}
                      disabled={closing}
                    >
                      <Lock className="h-4 w-4 me-2" />
                      Force Close
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
            <AlertDialogAction
              onClick={(e) => {
                // Keep the dialog open while the request is in flight — the
                // handler closes it on success (Radix otherwise auto-closes).
                e.preventDefault();
                handleClosePeriod();
              }}
              disabled={closing}
            >
              {closing ? "Closing..." : "Close Period"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* A152 item 3: force-close-with-reason when blocking checks fail */}
      <AlertDialog
        open={showForceDialog}
        onOpenChange={(o) => {
          setShowForceDialog(o);
          if (!o) setServerBlocking(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Force close {periodLabel}?</AlertDialogTitle>
            <AlertDialogDescription>
              The following checks must normally pass before closing. Closing anyway records your
              reason on the period-close for audit.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-3">
            <ul className="space-y-2 text-sm">
              {blockingFailures.map((c) => (
                <li key={c.check} className="rounded-md border border-red-200 bg-red-50 dark:bg-red-950/30 p-2">
                  <div className="flex items-center gap-2 font-medium text-red-700 dark:text-red-300">
                    <XCircle className="h-4 w-4" />
                    {c.title}
                  </div>
                  <p className="text-muted-foreground">{c.message}</p>
                </li>
              ))}
            </ul>
            <Textarea
              value={forceReason}
              onChange={(e) => setForceReason(e.target.value)}
              placeholder="Reason for closing despite the failing checks (required)"
              rows={3}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                // Stay open while in flight; a failed force keeps the typed
                // reason instead of discarding it with the dialog.
                e.preventDefault();
                handleClosePeriod({ force: true, reason: forceReason.trim() });
              }}
              disabled={closing || !forceReason.trim()}
            >
              {closing ? "Closing..." : "Force Close"}
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
