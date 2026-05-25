/**
 * /finance/exceptions — operator-visible projection failures (A80, 2026-05-25)
 *
 * Backs the "loud failures" principle from docs/finance_event_first_policy.md §8.
 * When a projection handler raises (instead of silently returning), the framework
 * writes a ProjectionFailureLog row that surfaces here so the operator sees
 * WHY their accounting data isn't being produced.
 *
 * Pre-A80: a Shopify projection failure meant the merchant saw empty pages with
 * no explanation. The whole 2026-05-25 A78 incident took 6+ hours to root-cause
 * because nothing surfaced the silent failure.
 *
 * Post-A80: failures show up here with category, message, fix_hint, occurrence
 * count, and a "Mark resolved" action.
 */

import { useEffect, useMemo, useState } from "react";
import type { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
  RefreshCw,
  XCircle,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

import { AppLayout } from "@/components/layout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/AuthContext";
import {
  projectionFailuresService,
  type FailureCategory,
  type ProjectionFailure,
  type ProjectionFailureDetail,
  type ProjectionFailureSummary,
} from "@/services/projection-failures.service";

// =============================================================================
// Visual helpers
// =============================================================================

const CATEGORY_ICON: Record<FailureCategory, JSX.Element> = {
  MISSING_CONFIG: <AlertCircle className="h-4 w-4" />,
  INVALID_DATA: <AlertTriangle className="h-4 w-4" />,
  DOWNSTREAM_FAILED: <XCircle className="h-4 w-4" />,
  UNEXPECTED: <Info className="h-4 w-4" />,
};

const CATEGORY_BADGE: Record<
  FailureCategory,
  "secondary" | "warning" | "destructive" | "outline"
> = {
  MISSING_CONFIG: "warning",
  INVALID_DATA: "warning",
  DOWNSTREAM_FAILED: "destructive",
  UNEXPECTED: "destructive",
};

const CATEGORY_HELP: Record<FailureCategory, string> = {
  MISSING_CONFIG:
    "Missing or incomplete company configuration (chart of accounts, posting profile, store wiring). Once you fix it, the next projection pass auto-recovers — these resolve themselves.",
  INVALID_DATA:
    "Event payload values don't permit producing a meaningful record (e.g., an order with zero totals). Either fix the source data in the upstream system or mark resolved if it should be intentionally skipped.",
  DOWNSTREAM_FAILED:
    "A downstream command refused the projection's call. Reason is in the message. Usually a code or config issue — once fixed and redeployed, the next pass auto-recovers.",
  UNEXPECTED:
    "Unhandled exception. Indicates a bug; file an engineering ticket with the message and the event payload.",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.floor(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  return `${days}d ago`;
}

// =============================================================================
// Page
// =============================================================================

export default function ExceptionsPage() {
  const { toast } = useToast();
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_staff || user?.is_superuser);

  const [summary, setSummary] = useState<ProjectionFailureSummary | null>(null);
  const [items, setItems] = useState<ProjectionFailure[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Filters
  const [resolvedFilter, setResolvedFilter] = useState<"true" | "false" | "all">(
    "false"
  );
  const [projectionFilter, setProjectionFilter] = useState<string>("");
  const [categoryFilter, setCategoryFilter] = useState<FailureCategory | "">("");

  // Expansion + actions
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detailById, setDetailById] = useState<Record<number, ProjectionFailureDetail>>({});
  const [loadingDetailId, setLoadingDetailId] = useState<number | null>(null);
  const [resolvingId, setResolvingId] = useState<number | null>(null);
  const [resolutionNote, setResolutionNote] = useState("");

  // =========================================================================
  // Data loading
  // =========================================================================

  const fetchAll = async () => {
    try {
      const [sum, list] = await Promise.all([
        projectionFailuresService.summary(),
        projectionFailuresService.list({
          resolved: resolvedFilter,
          projection_name: projectionFilter || undefined,
          category: (categoryFilter || undefined) as FailureCategory | undefined,
          limit: 100,
        }),
      ]);
      setSummary(sum);
      setItems(list.results);
      setTotalCount(list.total_count);
    } catch (err) {
      toast({
        title: "Failed to load exceptions",
        description: (err as Error).message || "Try refreshing.",
        variant: "destructive",
      });
    }
  };

  useEffect(() => {
    setLoading(true);
    fetchAll().finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolvedFilter, projectionFilter, categoryFilter]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchAll();
    setRefreshing(false);
  };

  const handleExpand = async (failure: ProjectionFailure) => {
    if (expandedId === failure.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(failure.id);
    if (!detailById[failure.id]) {
      setLoadingDetailId(failure.id);
      try {
        const detail = await projectionFailuresService.detail(failure.id);
        setDetailById((prev) => ({ ...prev, [failure.id]: detail }));
      } catch (err) {
        toast({
          title: "Failed to load detail",
          description: (err as Error).message,
          variant: "destructive",
        });
      } finally {
        setLoadingDetailId(null);
      }
    }
  };

  const handleResolve = async (failure: ProjectionFailure) => {
    setResolvingId(failure.id);
    try {
      await projectionFailuresService.resolve(failure.id, resolutionNote);
      toast({
        title: "Marked resolved",
        description: `${failure.projection_name} — ${failure.event_type}`,
      });
      setResolutionNote("");
      await fetchAll();
    } catch (err) {
      toast({
        title: "Failed to resolve",
        description: (err as Error).message,
        variant: "destructive",
      });
    } finally {
      setResolvingId(null);
    }
  };

  // =========================================================================
  // Summary cards (per-category counts)
  // =========================================================================

  const categoryCounts = useMemo(() => {
    const m: Record<FailureCategory, number> = {
      MISSING_CONFIG: 0,
      INVALID_DATA: 0,
      DOWNSTREAM_FAILED: 0,
      UNEXPECTED: 0,
    };
    summary?.by_category.forEach((row) => {
      m[row.category] = row.count;
    });
    return m;
  }, [summary]);

  // =========================================================================
  // Render
  // =========================================================================

  return (
    <AppLayout>
      <div className="space-y-6 p-6">
        <PageHeader
          title="Exceptions"
          subtitle="Projection failures that need operator attention"
          actions={
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefresh}
              disabled={refreshing || loading}
            >
              <RefreshCw
                className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`}
              />
              Refresh
            </Button>
          }
        />

        {/* Summary cards */}
        {summary && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Total Unresolved
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-3xl font-semibold">
                  {summary.total_unresolved}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {summary.total_unresolved === 0
                    ? "All clear — projections healthy."
                    : "Need operator review."}
                </p>
              </CardContent>
            </Card>

            {(
              [
                "MISSING_CONFIG",
                "INVALID_DATA",
                "DOWNSTREAM_FAILED",
              ] as FailureCategory[]
            ).map((cat) => (
              <Card key={cat}>
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                    {CATEGORY_ICON[cat]}
                    {cat.replace("_", " ")}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-3xl font-semibold">
                    {categoryCounts[cat]}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground line-clamp-2">
                    {CATEGORY_HELP[cat]}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {/* Filters */}
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 py-4">
            <div className="flex items-center gap-2">
              <label className="text-sm text-muted-foreground">Status:</label>
              <select
                value={resolvedFilter}
                onChange={(e) =>
                  setResolvedFilter(e.target.value as "true" | "false" | "all")
                }
                className="rounded border bg-background px-2 py-1 text-sm"
              >
                <option value="false">Open (unresolved)</option>
                <option value="true">Resolved</option>
                <option value="all">All</option>
              </select>
            </div>

            <div className="flex items-center gap-2">
              <label className="text-sm text-muted-foreground">Projection:</label>
              <select
                value={projectionFilter}
                onChange={(e) => setProjectionFilter(e.target.value)}
                className="rounded border bg-background px-2 py-1 text-sm"
              >
                <option value="">All projections</option>
                {summary?.by_projection.map((row) => (
                  <option key={row.projection_name} value={row.projection_name}>
                    {row.projection_name} ({row.count})
                  </option>
                ))}
              </select>
            </div>

            <div className="flex items-center gap-2">
              <label className="text-sm text-muted-foreground">Category:</label>
              <select
                value={categoryFilter}
                onChange={(e) =>
                  setCategoryFilter(e.target.value as FailureCategory | "")
                }
                className="rounded border bg-background px-2 py-1 text-sm"
              >
                <option value="">All categories</option>
                <option value="MISSING_CONFIG">Missing config</option>
                <option value="INVALID_DATA">Invalid data</option>
                <option value="DOWNSTREAM_FAILED">Downstream failed</option>
                <option value="UNEXPECTED">Unexpected</option>
              </select>
            </div>

            <div className="ml-auto text-sm text-muted-foreground">
              Showing {items.length} of {totalCount}
            </div>
          </CardContent>
        </Card>

        {/* List */}
        {loading ? (
          <Card>
            <CardContent className="flex justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : items.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
              <CheckCircle2 className="h-12 w-12 text-emerald-500" />
              <h3 className="text-lg font-medium">No exceptions to show</h3>
              <p className="text-sm text-muted-foreground">
                {resolvedFilter === "false"
                  ? "All projections are running cleanly. When something fails, it'll show up here."
                  : "No failures match the current filters."}
              </p>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-0">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="w-8" />
                    <th className="px-3 py-2 text-left">Projection</th>
                    <th className="px-3 py-2 text-left">Category</th>
                    <th className="px-3 py-2 text-left">Event type</th>
                    <th className="px-3 py-2 text-left">Message</th>
                    <th className="px-3 py-2 text-right">Occurrences</th>
                    <th className="px-3 py-2 text-right">Last seen</th>
                    <th className="px-3 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((f) => (
                    <FailureRow
                      key={f.id}
                      failure={f}
                      isExpanded={expandedId === f.id}
                      detail={detailById[f.id]}
                      loadingDetail={loadingDetailId === f.id}
                      isAdmin={isAdmin}
                      isResolving={resolvingId === f.id}
                      resolutionNote={resolutionNote}
                      onResolutionNoteChange={setResolutionNote}
                      onExpand={() => handleExpand(f)}
                      onResolve={() => handleResolve(f)}
                    />
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}
      </div>
    </AppLayout>
  );
}

// =============================================================================
// Row component
// =============================================================================

interface FailureRowProps {
  failure: ProjectionFailure;
  isExpanded: boolean;
  detail: ProjectionFailureDetail | undefined;
  loadingDetail: boolean;
  isAdmin: boolean;
  isResolving: boolean;
  resolutionNote: string;
  onResolutionNoteChange: (s: string) => void;
  onExpand: () => void;
  onResolve: () => void;
}

function FailureRow({
  failure,
  isExpanded,
  detail,
  loadingDetail,
  isAdmin,
  isResolving,
  resolutionNote,
  onResolutionNoteChange,
  onExpand,
  onResolve,
}: FailureRowProps) {
  return (
    <>
      <tr className="border-t hover:bg-muted/30">
        <td className="px-2 py-2 align-top">
          <button
            onClick={onExpand}
            className="inline-flex items-center text-muted-foreground hover:text-foreground"
            aria-label={isExpanded ? "Collapse" : "Expand"}
          >
            {isExpanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </button>
        </td>
        <td className="px-3 py-2 align-top font-mono text-xs">
          {failure.projection_name}
        </td>
        <td className="px-3 py-2 align-top">
          <Badge variant={CATEGORY_BADGE[failure.category]}>
            <span className="inline-flex items-center gap-1">
              {CATEGORY_ICON[failure.category]}
              {failure.category_display}
            </span>
          </Badge>
        </td>
        <td className="px-3 py-2 align-top font-mono text-xs">
          {failure.event_type}
        </td>
        <td className="px-3 py-2 align-top max-w-md">
          <div className="line-clamp-2">{failure.message}</div>
        </td>
        <td className="px-3 py-2 align-top text-right tabular-nums">
          {failure.occurrence_count}
        </td>
        <td className="px-3 py-2 align-top text-right text-muted-foreground">
          {timeAgo(failure.last_seen_at)}
        </td>
        <td className="px-3 py-2 align-top text-right">
          {failure.resolved ? (
            <Badge variant="outline">Resolved</Badge>
          ) : isAdmin ? (
            <Button
              size="sm"
              variant="outline"
              onClick={onResolve}
              disabled={isResolving}
            >
              {isResolving ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                "Mark resolved"
              )}
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">Admin only</span>
          )}
        </td>
      </tr>
      {isExpanded && (
        <tr className="border-t bg-muted/20">
          <td colSpan={8} className="p-4">
            {loadingDetail ? (
              <div className="flex justify-center py-4">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : detail ? (
              <div className="space-y-4">
                {detail.fix_hint && (
                  <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-900 dark:bg-amber-950">
                    <div className="mb-1 font-medium">Fix hint</div>
                    <div className="text-muted-foreground">{detail.fix_hint}</div>
                  </div>
                )}

                <div>
                  <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                    Full message
                  </div>
                  <pre className="whitespace-pre-wrap rounded border bg-background p-3 font-mono text-xs">
                    {detail.message}
                  </pre>
                </div>

                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <div>
                    <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                      Event reference
                    </div>
                    <div className="font-mono text-xs">
                      <div>id: {detail.event_id}</div>
                      <div>type: {detail.event_type}</div>
                      <div>
                        aggregate: {detail.event_aggregate_type}#
                        {detail.event_aggregate_id}
                      </div>
                    </div>
                  </div>
                  <div>
                    <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                      Failure timeline
                    </div>
                    <div className="font-mono text-xs">
                      <div>first: {detail.first_seen_at}</div>
                      <div>last: {detail.last_seen_at}</div>
                      <div>count: {detail.occurrence_count}</div>
                    </div>
                  </div>
                </div>

                <div>
                  <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                    Event payload
                  </div>
                  <pre className="max-h-64 overflow-auto rounded border bg-background p-3 font-mono text-xs">
                    {JSON.stringify(detail.event_data, null, 2)}
                  </pre>
                </div>

                {detail.resolved && (
                  <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-sm dark:border-emerald-900 dark:bg-emerald-950">
                    <div className="font-medium">
                      Resolved by {detail.resolved_by_name || "unknown"}{" "}
                      {detail.resolved_at && `at ${detail.resolved_at}`}
                    </div>
                    {detail.resolution_note && (
                      <div className="mt-1 text-muted-foreground">
                        {detail.resolution_note}
                      </div>
                    )}
                  </div>
                )}

                {!detail.resolved && isAdmin && (
                  <div className="rounded border p-3">
                    <div className="mb-2 text-xs font-medium uppercase text-muted-foreground">
                      Resolution note (optional)
                    </div>
                    <textarea
                      value={resolutionNote}
                      onChange={(e) => onResolutionNoteChange(e.target.value)}
                      rows={2}
                      placeholder="Why did you resolve this? e.g., 'Added missing SALES_REVENUE mapping via Setup → Account Mapping.'"
                      className="w-full rounded border bg-background p-2 text-sm"
                    />
                  </div>
                )}
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">
                Click to load details.
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// =============================================================================
// i18n
// =============================================================================

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
