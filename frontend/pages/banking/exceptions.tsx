import { useState, useEffect, useCallback } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  AlertTriangle,
  Loader2,
  Search,
  RefreshCw,
  CheckCircle2,
  XCircle,
  ArrowUpCircle,
  Clock,
  Filter,
  ChevronDown,
  ChevronRight,
  RotateCcw,
  Ban,
  User,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  bankService,
  ReconciliationException,
  ExceptionSummary,
  ExceptionStatus,
  ExceptionSeverity,
  ExceptionType,
} from "@/services/bank.service";

// =============================================================================
// Helpers
// =============================================================================

function severityColor(severity: ExceptionSeverity) {
  switch (severity) {
    case "CRITICAL":
      return "destructive";
    case "HIGH":
      return "warning";
    case "MEDIUM":
      return "secondary";
    case "LOW":
      return "outline";
    default:
      return "secondary";
  }
}

function severityTextColor(severity: ExceptionSeverity) {
  switch (severity) {
    case "CRITICAL":
      return "text-red-600";
    case "HIGH":
      return "text-orange-600";
    case "MEDIUM":
      return "text-yellow-600";
    case "LOW":
      return "text-muted-foreground";
    default:
      return "";
  }
}

function statusIcon(s: ExceptionStatus) {
  switch (s) {
    case "OPEN":
      return <Clock className="h-3.5 w-3.5 text-blue-500" />;
    case "IN_PROGRESS":
      return <Loader2 className="h-3.5 w-3.5 text-yellow-500" />;
    case "ESCALATED":
      return <ArrowUpCircle className="h-3.5 w-3.5 text-red-500" />;
    case "RESOLVED":
      return <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />;
    case "DISMISSED":
      return <Ban className="h-3.5 w-3.5 text-muted-foreground" />;
  }
}

function statusLabel(s: ExceptionStatus) {
  return s.replace("_", " ");
}

function typeLabel(t: ExceptionType) {
  const map: Record<ExceptionType, string> = {
    UNMATCHED_BANK_TX: "Unmatched Bank Tx",
    UNMATCHED_PAYOUT: "Unmatched Payout",
    PAYOUT_DISCREPANCY: "Payout Discrepancy",
    CLEARING_BALANCE: "Clearing Balance",
    MISSING_JE: "Missing JE",
    FEE_VARIANCE: "Fee Variance",
    DUPLICATE_MATCH: "Duplicate Match",
  };
  return map[t] || t;
}

// =============================================================================
// Summary Cards
// =============================================================================

function SummaryCards({ summary }: { summary: ExceptionSummary | null }) {
  if (!summary) return null;

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
      <Card>
        <CardContent className="pt-4 pb-3 text-center">
          <p className="text-xs font-medium text-muted-foreground mb-1">Open</p>
          <p className="text-2xl font-bold">{summary.total_open}</p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4 pb-3 text-center">
          <p className="text-xs font-medium text-muted-foreground mb-1">Critical</p>
          <p className="text-2xl font-bold text-red-600">
            {summary.by_severity.CRITICAL || 0}
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4 pb-3 text-center">
          <p className="text-xs font-medium text-muted-foreground mb-1">High</p>
          <p className="text-2xl font-bold text-orange-600">
            {summary.by_severity.HIGH || 0}
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4 pb-3 text-center">
          <p className="text-xs font-medium text-muted-foreground mb-1">Resolved</p>
          <p className="text-2xl font-bold text-green-600">{summary.total_resolved}</p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4 pb-3 text-center">
          <p className="text-xs font-medium text-muted-foreground mb-1">Dismissed</p>
          <p className="text-2xl font-bold text-muted-foreground">
            {summary.total_dismissed}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

// =============================================================================
// Exception Detail Panel
// =============================================================================

function ExceptionDetailPanel({
  exception,
  onAction,
  acting,
}: {
  exception: ReconciliationException;
  onAction: (action: string, note?: string) => void;
  acting: boolean;
}) {
  const [note, setNote] = useState("");
  const e = exception;

  return (
    <div className="border rounded-lg bg-muted/30 p-4 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Badge variant={severityColor(e.severity) as any}>{e.severity}</Badge>
            <Badge variant="outline">{typeLabel(e.exception_type)}</Badge>
            {e.platform && (
              <Badge variant="secondary" className="capitalize">
                {e.platform}
              </Badge>
            )}
          </div>
          <h3 className="font-semibold text-sm">{e.title}</h3>
          <p className="text-xs text-muted-foreground">{e.description}</p>
        </div>
        <div className="text-right text-xs text-muted-foreground whitespace-nowrap">
          <p>{e.exception_date}</p>
          {e.amount && (
            <p className="font-mono font-medium text-sm text-foreground">
              {Number(e.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}{" "}
              {e.currency}
            </p>
          )}
        </div>
      </div>

      {/* Details JSON */}
      {e.details && Object.keys(e.details).length > 0 && (
        <div className="bg-background rounded border p-3">
          <p className="text-xs font-medium text-muted-foreground mb-2">Details</p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            {Object.entries(e.details).map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-muted-foreground">{k.replace(/_/g, " ")}:</span>
                <span className="font-mono">{String(v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reference */}
      {e.reference_label && (
        <p className="text-xs text-muted-foreground">
          Reference: <span className="font-mono">{e.reference_label}</span>
        </p>
      )}

      {/* Resolution note */}
      {e.resolution_note && (
        <div className="bg-green-50 dark:bg-green-950/20 rounded border border-green-200 dark:border-green-900 p-3">
          <p className="text-xs font-medium text-green-700 dark:text-green-400 mb-1">
            Resolution Note
          </p>
          <p className="text-xs">{e.resolution_note}</p>
        </div>
      )}

      {/* Actions */}
      {(e.status === "OPEN" || e.status === "IN_PROGRESS" || e.status === "ESCALATED") && (
        <div className="space-y-3 pt-2 border-t">
          <textarea
            className="w-full text-xs border rounded p-2 bg-background resize-none"
            rows={2}
            placeholder="Resolution note (optional)..."
            value={note}
            onChange={(ev) => setNote(ev.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="default"
              disabled={acting}
              onClick={() => onAction("resolve", note)}
            >
              <CheckCircle2 className="h-3.5 w-3.5 mr-1" /> Resolve
            </Button>
            {e.status !== "ESCALATED" && (
              <Button
                size="sm"
                variant="outline"
                disabled={acting}
                onClick={() => onAction("escalate")}
              >
                <ArrowUpCircle className="h-3.5 w-3.5 mr-1" /> Escalate
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={acting}
              onClick={() => onAction("dismiss", note)}
            >
              <Ban className="h-3.5 w-3.5 mr-1" /> Dismiss
            </Button>
          </div>
        </div>
      )}

      {/* Reopen for resolved/dismissed */}
      {(e.status === "RESOLVED" || e.status === "DISMISSED") && (
        <div className="pt-2 border-t">
          <Button
            size="sm"
            variant="outline"
            disabled={acting}
            onClick={() => onAction("reopen")}
          >
            <RotateCcw className="h-3.5 w-3.5 mr-1" /> Reopen
          </Button>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Main Page
// =============================================================================

export default function ExceptionsPage() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [acting, setActing] = useState(false);
  const [exceptions, setExceptions] = useState<ReconciliationException[]>([]);
  const [summary, setSummary] = useState<ExceptionSummary | null>(null);
  const [total, setTotal] = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState<ExceptionStatus | "">("");
  const [severityFilter, setSeverityFilter] = useState<ExceptionSeverity | "">("");
  const [typeFilter, setTypeFilter] = useState<ExceptionType | "">("");
  const [search, setSearch] = useState("");

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = {};
      if (statusFilter) params.status = statusFilter;
      if (severityFilter) params.severity = severityFilter;
      if (typeFilter) params.type = typeFilter;
      if (search) params.search = search;

      const [listRes, summaryRes] = await Promise.all([
        bankService.getExceptions(params),
        bankService.getExceptionSummary(),
      ]);
      setExceptions(listRes.data.results);
      setTotal(listRes.data.total);
      setSummary(summaryRes.data);
    } catch {
      toast({ title: "Failed to load exceptions", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [statusFilter, severityFilter, typeFilter, search, toast]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleScan = async () => {
    setScanning(true);
    try {
      const res = await bankService.scanExceptions();
      toast({
        title: "Scan complete",
        description: `${res.data.created} new, ${res.data.resolved} auto-resolved, ${res.data.open} open`,
      });
      fetchData();
    } catch {
      toast({ title: "Scan failed", variant: "destructive" });
    } finally {
      setScanning(false);
    }
  };

  const handleAction = async (id: number, action: string, note?: string) => {
    setActing(true);
    try {
      if (action === "resolve") {
        await bankService.resolveException(id, note);
      } else if (action === "escalate") {
        await bankService.escalateException(id);
      } else if (action === "dismiss") {
        await bankService.dismissException(id, note);
      } else if (action === "reopen") {
        await bankService.reopenException(id);
      }
      toast({ title: `Exception ${action}d` });
      fetchData();
    } catch {
      toast({ title: `Failed to ${action}`, variant: "destructive" });
    } finally {
      setActing(false);
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <PageHeader
            title="Exception Queue"
            subtitle="Reconciliation exceptions requiring review"
          />
          <Button onClick={handleScan} disabled={scanning} variant="outline">
            {scanning ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4 mr-2" />
            )}
            Scan Now
          </Button>
        </div>

        <SummaryCards summary={summary} />

        {/* Filters */}
        <Card>
          <CardContent className="pt-4 pb-3">
            <div className="flex flex-wrap items-center gap-3">
              <div className="relative flex-1 min-w-[200px]">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <input
                  type="text"
                  className="w-full pl-9 pr-3 py-2 text-sm border rounded-md bg-background"
                  placeholder="Search exceptions..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <select
                className="text-sm border rounded-md px-3 py-2 bg-background"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as ExceptionStatus | "")}
              >
                <option value="">All Statuses</option>
                <option value="OPEN">Open</option>
                <option value="IN_PROGRESS">In Progress</option>
                <option value="ESCALATED">Escalated</option>
                <option value="RESOLVED">Resolved</option>
                <option value="DISMISSED">Dismissed</option>
              </select>
              <select
                className="text-sm border rounded-md px-3 py-2 bg-background"
                value={severityFilter}
                onChange={(e) =>
                  setSeverityFilter(e.target.value as ExceptionSeverity | "")
                }
              >
                <option value="">All Severities</option>
                <option value="CRITICAL">Critical</option>
                <option value="HIGH">High</option>
                <option value="MEDIUM">Medium</option>
                <option value="LOW">Low</option>
              </select>
              <select
                className="text-sm border rounded-md px-3 py-2 bg-background"
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value as ExceptionType | "")}
              >
                <option value="">All Types</option>
                <option value="UNMATCHED_BANK_TX">Unmatched Bank Tx</option>
                <option value="UNMATCHED_PAYOUT">Unmatched Payout</option>
                <option value="PAYOUT_DISCREPANCY">Payout Discrepancy</option>
                <option value="FEE_VARIANCE">Fee Variance</option>
                <option value="MISSING_JE">Missing JE</option>
                <option value="CLEARING_BALANCE">Clearing Balance</option>
                <option value="DUPLICATE_MATCH">Duplicate Match</option>
              </select>
            </div>
          </CardContent>
        </Card>

        {/* Exception List */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <AlertTriangle className="h-4 w-4" />
                Exceptions ({total})
              </CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : exceptions.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground text-sm">
                <CheckCircle2 className="h-8 w-8 mx-auto mb-2 text-green-500" />
                No exceptions found. All clear!
              </div>
            ) : (
              <div className="space-y-2">
                {exceptions.map((exc) => (
                  <div key={exc.id}>
                    {/* Row */}
                    <button
                      className="w-full text-left flex items-center gap-3 px-3 py-2.5 rounded-md hover:bg-muted/50 transition-colors"
                      onClick={() =>
                        setExpandedId(expandedId === exc.id ? null : exc.id)
                      }
                    >
                      {expandedId === exc.id ? (
                        <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />
                      ) : (
                        <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
                      )}
                      {statusIcon(exc.status)}
                      <Badge
                        variant={severityColor(exc.severity) as any}
                        className="text-[10px] px-1.5 py-0"
                      >
                        {exc.severity}
                      </Badge>
                      <span className="text-sm font-medium truncate flex-1">
                        {exc.title}
                      </span>
                      {exc.platform && (
                        <Badge variant="outline" className="text-[10px] capitalize">
                          {exc.platform}
                        </Badge>
                      )}
                      <span className="text-xs text-muted-foreground whitespace-nowrap">
                        {exc.exception_date}
                      </span>
                      {exc.amount && (
                        <span className="text-xs font-mono font-medium whitespace-nowrap">
                          {Number(exc.amount).toLocaleString(undefined, {
                            minimumFractionDigits: 2,
                          })}{" "}
                          {exc.currency}
                        </span>
                      )}
                    </button>

                    {/* Expanded detail */}
                    {expandedId === exc.id && (
                      <div className="ml-8 mt-1 mb-3">
                        <ExceptionDetailPanel
                          exception={exc}
                          acting={acting}
                          onAction={(action, note) =>
                            handleAction(exc.id, action, note)
                          }
                        />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale ?? "en", ["common"])),
  },
});
