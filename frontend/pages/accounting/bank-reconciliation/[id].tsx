import { useState, useEffect, useCallback } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Link2,
  Unlink,
  Zap,
  Ban,
  Loader2,
  Lock,
  AlertTriangle,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  bankReconciliationService,
  BankStatementDetail,
  BankStatementLineData,
  UnreconciledJournalLine,
} from "@/services/bank-reconciliation.service";

function formatMoney(value: string | number, currency = "USD") {
  const n = typeof value === "string" ? Number(value) : value;
  return `${currency} ${n.toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
}

const MATCH_BADGE: Record<string, { label: string; color: string }> = {
  UNMATCHED: { label: "Unmatched", color: "bg-red-100 text-red-700" },
  AUTO_MATCHED: { label: "Auto", color: "bg-green-100 text-green-700" },
  MANUAL_MATCHED: { label: "Manual", color: "bg-blue-100 text-blue-700" },
  EXCLUDED: { label: "Excluded", color: "bg-gray-100 text-gray-500" },
};

export default function BankStatementDetailPage() {
  const router = useRouter();
  const { toast } = useToast();
  const id = Number(router.query.id);

  const [statement, setStatement] = useState<BankStatementDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [autoMatching, setAutoMatching] = useState(false);
  const [reconciling, setReconciling] = useState(false);

  // Manual match state
  const [selectedBankLine, setSelectedBankLine] = useState<number | null>(null);
  const [unreconciledLines, setUnreconciledLines] = useState<UnreconciledJournalLine[]>([]);
  const [loadingJL, setLoadingJL] = useState(false);

  const fetchStatement = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const { data } = await bankReconciliationService.getStatement(id);
      setStatement(data);
    } catch {
      toast({ title: "Failed to load statement.", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetchStatement();
  }, [fetchStatement]);

  const handleAutoMatch = async () => {
    setAutoMatching(true);
    try {
      const { data } = await bankReconciliationService.autoMatch(id);
      toast({ title: `Auto-matched ${data.matched} of ${data.total} lines.` });
      fetchStatement();
    } catch {
      toast({ title: "Auto-match failed.", variant: "destructive" });
    } finally {
      setAutoMatching(false);
    }
  };

  const handleUnmatch = async (bankLineId: number) => {
    try {
      await bankReconciliationService.unmatch(bankLineId);
      toast({ title: "Line unmatched." });
      fetchStatement();
    } catch {
      toast({ title: "Failed to unmatch.", variant: "destructive" });
    }
  };

  const handleExclude = async (bankLineId: number) => {
    try {
      await bankReconciliationService.exclude(bankLineId);
      toast({ title: "Line excluded." });
      fetchStatement();
    } catch {
      toast({ title: "Failed to exclude.", variant: "destructive" });
    }
  };

  const handleSelectForMatch = async (bankLineId: number) => {
    setSelectedBankLine(bankLineId);
    if (!statement) return;
    setLoadingJL(true);
    try {
      const { data } = await bankReconciliationService.getUnreconciledLines(
        statement.account_id,
        statement.period_end,
      );
      setUnreconciledLines(data);
    } catch {
      toast({ title: "Failed to load journal lines.", variant: "destructive" });
    } finally {
      setLoadingJL(false);
    }
  };

  const handleManualMatch = async (journalLineId: number) => {
    if (!selectedBankLine) return;
    try {
      await bankReconciliationService.manualMatch(selectedBankLine, journalLineId);
      toast({ title: "Lines matched." });
      setSelectedBankLine(null);
      setUnreconciledLines([]);
      fetchStatement();
    } catch {
      toast({ title: "Failed to match.", variant: "destructive" });
    }
  };

  const handleReconcile = async () => {
    setReconciling(true);
    try {
      const { data } = await bankReconciliationService.reconcile(id);
      toast({
        title:
          Number(data.difference) === 0
            ? "Statement reconciled successfully!"
            : `Reconciled with difference of ${data.difference}`,
      });
      fetchStatement();
    } catch {
      toast({ title: "Failed to reconcile.", variant: "destructive" });
    } finally {
      setReconciling(false);
    }
  };

  if (loading || !statement) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center py-24">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </AppLayout>
    );
  }

  const { summary } = statement;
  const isReconciled = statement.status === "RECONCILED";
  const difference = Number(summary.difference);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={`${statement.account_code} — ${statement.statement_date}`}
          subtitle={`${statement.account_name} | ${statement.period_start} to ${statement.period_end}`}
          actions={
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => router.push("/accounting/bank-reconciliation")}
              >
                <ArrowLeft className="me-2 h-4 w-4" />
                Back
              </Button>
              {!isReconciled && (
                <>
                  <Button onClick={handleAutoMatch} disabled={autoMatching}>
                    {autoMatching ? (
                      <Loader2 className="me-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Zap className="me-2 h-4 w-4" />
                    )}
                    Auto-Match
                  </Button>
                  <Button
                    onClick={handleReconcile}
                    disabled={reconciling}
                    variant={difference === 0 ? "default" : "outline"}
                  >
                    {reconciling ? (
                      <Loader2 className="me-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Lock className="me-2 h-4 w-4" />
                    )}
                    Complete Reconciliation
                  </Button>
                </>
              )}
            </div>
          }
        />

        {/* Reconciliation Summary */}
        <Card>
          <CardHeader>
            <CardTitle>Reconciliation Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <p className="text-sm text-muted-foreground">Statement Closing</p>
                <p className="font-mono font-medium">
                  {formatMoney(summary.statement_closing_balance, statement.currency)}
                </p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">GL Balance</p>
                <p className="font-mono font-medium">
                  {formatMoney(summary.gl_balance, statement.currency)}
                </p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">
                  Outstanding (Deposits / Withdrawals)
                </p>
                <p className="font-mono font-medium text-muted-foreground">
                  +{Number(summary.outstanding_deposits).toFixed(2)} / -{Number(summary.outstanding_withdrawals).toFixed(2)}
                </p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Difference</p>
                <p
                  className={`font-mono font-bold text-lg ${
                    difference === 0
                      ? "text-green-600"
                      : "text-red-600"
                  }`}
                >
                  {formatMoney(summary.difference, statement.currency)}
                </p>
                {difference !== 0 && (
                  <p className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
                    <AlertTriangle className="h-3 w-3" />
                    Must be 0 to reconcile cleanly
                  </p>
                )}
              </div>
            </div>
            <div className="mt-4 flex gap-6 text-sm text-muted-foreground border-t pt-3">
              <span>
                Matched: <strong>{summary.matched_count}</strong> / {summary.total_lines}
              </span>
              <span>
                Unmatched: <strong>{summary.unmatched_count}</strong>
              </span>
            </div>
          </CardContent>
        </Card>

        {/* Bank Statement Lines */}
        <Card>
          <CardHeader>
            <CardTitle>Statement Lines</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 font-medium">Date</th>
                    <th className="pb-2 font-medium">Description</th>
                    <th className="pb-2 font-medium">Reference</th>
                    <th className="pb-2 font-medium text-right">Amount</th>
                    <th className="pb-2 font-medium text-center">Status</th>
                    <th className="pb-2 font-medium">Matched To</th>
                    <th className="pb-2 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {statement.lines.map((line) => {
                    const badge = MATCH_BADGE[line.match_status] || MATCH_BADGE.UNMATCHED;
                    const amt = Number(line.amount);
                    return (
                      <tr
                        key={line.id}
                        className={`border-b last:border-0 ${
                          selectedBankLine === line.id ? "bg-blue-50" : ""
                        }`}
                      >
                        <td className="py-2">{line.line_date}</td>
                        <td className="py-2 max-w-[200px] truncate">
                          {line.description}
                        </td>
                        <td className="py-2 text-muted-foreground">
                          {line.reference}
                        </td>
                        <td
                          className={`py-2 text-right font-mono ${
                            amt >= 0 ? "text-green-700" : "text-red-700"
                          }`}
                        >
                          {amt >= 0 ? "+" : ""}
                          {amt.toFixed(2)}
                        </td>
                        <td className="py-2 text-center">
                          <span
                            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${badge.color}`}
                          >
                            {line.match_status === "UNMATCHED" ? (
                              <XCircle className="h-3 w-3" />
                            ) : (
                              <CheckCircle2 className="h-3 w-3" />
                            )}
                            {badge.label}
                          </span>
                        </td>
                        <td className="py-2 text-xs text-muted-foreground">
                          {line.matched_journal_line ? (
                            <span>
                              {line.matched_journal_line.entry_number}{" "}
                              {line.matched_journal_line.entry_date}
                            </span>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="py-2 text-right">
                          {!isReconciled && (
                            <div className="flex gap-1 justify-end">
                              {line.match_status === "UNMATCHED" && (
                                <>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => handleSelectForMatch(line.id)}
                                    title="Manual match"
                                  >
                                    <Link2 className="h-3.5 w-3.5" />
                                  </Button>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => handleExclude(line.id)}
                                    title="Exclude"
                                  >
                                    <Ban className="h-3.5 w-3.5" />
                                  </Button>
                                </>
                              )}
                              {(line.match_status === "AUTO_MATCHED" ||
                                line.match_status === "MANUAL_MATCHED") && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => handleUnmatch(line.id)}
                                  title="Unmatch"
                                >
                                  <Unlink className="h-3.5 w-3.5" />
                                </Button>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* Manual Match Panel */}
        {selectedBankLine && (
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Select Journal Line to Match</CardTitle>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSelectedBankLine(null);
                  setUnreconciledLines([]);
                }}
              >
                Cancel
              </Button>
            </CardHeader>
            <CardContent>
              {loadingJL ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="h-5 w-5 animate-spin" />
                </div>
              ) : unreconciledLines.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4">
                  No unreconciled journal lines found for this account.
                </p>
              ) : (
                <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-background">
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="pb-2 font-medium">Date</th>
                        <th className="pb-2 font-medium">Entry #</th>
                        <th className="pb-2 font-medium">Memo</th>
                        <th className="pb-2 font-medium">Description</th>
                        <th className="pb-2 font-medium text-right">Net Amount</th>
                        <th className="pb-2 font-medium"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {unreconciledLines.map((jl) => (
                        <tr
                          key={jl.id}
                          className="border-b last:border-0 hover:bg-muted/50"
                        >
                          <td className="py-2">{jl.entry_date}</td>
                          <td className="py-2 font-mono text-xs">
                            {jl.entry_number}
                          </td>
                          <td className="py-2 max-w-[150px] truncate">
                            {jl.entry_memo}
                          </td>
                          <td className="py-2 max-w-[150px] truncate text-muted-foreground">
                            {jl.description}
                          </td>
                          <td className="py-2 text-right font-mono">
                            {Number(jl.net_amount).toFixed(2)}
                          </td>
                          <td className="py-2 text-right">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleManualMatch(jl.id)}
                            >
                              <Link2 className="me-1 h-3.5 w-3.5" />
                              Match
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale ?? "en", ["common"])),
  },
});
