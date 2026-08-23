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

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
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
import {
  importRejectedRowsService,
  type ImportRejectedRow,
} from "@/services/import-rejected-rows.service";
import {
  shopifyRejectedEvidenceService,
  type ShopifyRejectedEvidence,
} from "@/services/shopify-rejected-evidence.service";

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
  const { user, membership } = useAuth();
  // F22: company OWNER/ADMIN can resolve their own exceptions (matches the
  // backend gate) — previously is_staff-only, so merchants saw "Admin only".
  const isAdmin = Boolean(
    user?.is_staff || user?.is_superuser || membership?.role === "OWNER" || membership?.role === "ADMIN"
  );

  const [summary, setSummary] = useState<ProjectionFailureSummary | null>(null);
  const [items, setItems] = useState<ProjectionFailure[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // A5-PR3: durable per-row import rejections (settlement / bank CSV).
  const [importRejects, setImportRejects] = useState<ImportRejectedRow[]>([]);
  // Filtered count (respects the Open/Resolved/All filter) — drives the queue.
  const [importRejectsCount, setImportRejectsCount] = useState(0);
  // Always-unresolved count (filter-independent) — drives the "Total Unresolved"
  // summary card, which pairs with the always-unresolved projection summary
  // (Codex round-5: the filtered count would inflate the total under Resolved/All).
  const [unresolvedRejectsCount, setUnresolvedRejectsCount] = useState(0);
  // Keyset-paged "Load more" for the reject queue (Codex round-7/8/9): a growing
  // single-request limit hits the endpoint's 500 cap, and offset paging skips/dups
  // rows when the set mutates between fetches — so page by the server's next_cursor
  // over an immutable key. `null` cursor ⇒ no more pages ⇒ hide the button.
  const [loadingMoreRejects, setLoadingMoreRejects] = useState(false);
  const [rejectNextCursor, setRejectNextCursor] = useState<string | null>(null);
  // `reloading` is true while ANY reset (initial load, filter change, refresh, or a
  // resolve-triggered refetch) is in flight — every reset goes through fetchAll, so
  // this uniformly disables "Load more" during a reset.
  const [reloading, setReloading] = useState(false);
  // Monotonic fetch generation: a reset bumps it so a slow in-flight "Load more"
  // started BEFORE the reset is discarded instead of appended to the reset list.
  const fetchGenRef = useRef(0);
  // Synchronous "a reset is in flight" flag (a ref, not state, so the load-more
  // handler reads the live value with no render/batching lag): blocks a load-more
  // from STARTING during a reset — the case the generation check alone misses,
  // because a load-more begun mid-reset would capture that reset's own generation
  // (Codex round-10).
  const resetInFlightRef = useRef(false);
  const [resolvingRejectId, setResolvingRejectId] = useState<number | null>(null);
  const [expandedRejectId, setExpandedRejectId] = useState<number | null>(null);

  // A5-PR2b: rejected Shopify source evidence — authenticated ORDER/REFUND
  // payloads durably rejected at ingress (no order/refund row, no event). A
  // clearly-typed third sibling queue; same keyset/generation discipline as the
  // import-reject card.
  const [shopifyEvidence, setShopifyEvidence] = useState<ShopifyRejectedEvidence[]>([]);
  // Filtered count (respects the Open/Resolved/All filter) — drives the queue.
  const [shopifyEvidenceCount, setShopifyEvidenceCount] = useState(0);
  // Always-open count (filter-independent) — drives the "Total Unresolved" fold.
  const [openEvidenceCount, setOpenEvidenceCount] = useState(0);
  const [loadingMoreEvidence, setLoadingMoreEvidence] = useState(false);
  const [evidenceNextCursor, setEvidenceNextCursor] = useState<string | null>(null);
  const [ackingEvidenceId, setAckingEvidenceId] = useState<number | null>(null);
  const [expandedEvidenceId, setExpandedEvidenceId] = useState<number | null>(null);

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
    // A reset fetch: bump the generation so any in-flight "Load more" is discarded,
    // and mark a reset in flight so a NEW "Load more" can't start mid-reset.
    const gen = ++fetchGenRef.current;
    resetInFlightRef.current = true;
    setReloading(true);
    try {
      const [sum, list, rejects, unresolvedRejects, evidence, openEvidence] = await Promise.all([
        projectionFailuresService.summary(),
        projectionFailuresService.list({
          resolved: resolvedFilter,
          projection_name: projectionFilter || undefined,
          category: (categoryFilter || undefined) as FailureCategory | undefined,
          limit: 100,
        }),
        // First keyset page (no cursor) — resets the reject queue to the newest page.
        importRejectedRowsService.list({ resolved: resolvedFilter, limit: 100 }),
        // Filter-independent unresolved count for the summary card (see state).
        importRejectedRowsService.list({ resolved: "false", limit: 1 }),
        // A5-PR2b: the Shopify evidence queue's acknowledged filter is
        // tri-state and maps 1:1 onto the page filter ("true" = closed:
        // acknowledged OR healed/superseded).
        shopifyRejectedEvidenceService.list({
          acknowledged: resolvedFilter,
          limit: 100,
        }),
        // Filter-independent OPEN count for the summary fold.
        shopifyRejectedEvidenceService.list({ acknowledged: "false", limit: 1 }),
      ]);
      // A newer fetch (filter change) superseded this one — drop the stale result.
      if (gen !== fetchGenRef.current) return;
      setSummary(sum);
      setItems(list.results);
      setTotalCount(list.total_count);
      setImportRejects(rejects.results);
      setImportRejectsCount(rejects.total_count);
      setRejectNextCursor(rejects.next_cursor);
      setUnresolvedRejectsCount(unresolvedRejects.total_count);
      setShopifyEvidence(evidence.results);
      setShopifyEvidenceCount(evidence.total_count);
      setEvidenceNextCursor(evidence.next_cursor);
      setOpenEvidenceCount(openEvidence.total_count);
    } catch (err) {
      if (gen !== fetchGenRef.current) return;
      toast({
        title: "Failed to load exceptions",
        description: (err as Error).message || "Try refreshing.",
        variant: "destructive",
      });
    } finally {
      // Only the LATEST reset clears the flags — a superseded reset must not
      // re-enable load-more while a newer reset is still running.
      if (gen === fetchGenRef.current) {
        resetInFlightRef.current = false;
        setReloading(false);
      }
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

  // Append the next keyset page (cursor = the previous page's next_cursor). Immutable
  // -id ordering means a row resolved/re-seen between fetches can't skip or dup rows;
  // dedup-on-append is a belt-and-braces guard. The generation check discards a page
  // whose filter was changed out from under it mid-flight (Codex round-9).
  const handleLoadMoreRejects = async () => {
    // Never start a page while a reset is in flight: a load-more begun mid-reset
    // would capture that reset's generation and slip past the check below, appending
    // an old-cursor page onto the freshly reset list (Codex round-10).
    if (!rejectNextCursor || resetInFlightRef.current) return;
    const gen = fetchGenRef.current; // capture; a "load more" is not a reset
    const cursor = rejectNextCursor;
    setLoadingMoreRejects(true);
    try {
      const next = await importRejectedRowsService.list({
        resolved: resolvedFilter,
        limit: 100,
        cursor,
      });
      if (gen !== fetchGenRef.current) return; // a reset started after us — discard
      setImportRejects((prev) => {
        const seen = new Set(prev.map((r) => r.id));
        return [...prev, ...next.results.filter((r) => !seen.has(r.id))];
      });
      setImportRejectsCount(next.total_count);
      setRejectNextCursor(next.next_cursor);
    } catch (err) {
      if (gen !== fetchGenRef.current) return;
      toast({
        title: "Failed to load more import rejections",
        description: (err as Error).message || "Try refreshing.",
        variant: "destructive",
      });
    } finally {
      setLoadingMoreRejects(false);
    }
  };

  // A5-PR2b: append the next keyset page of Shopify rejected evidence — same
  // reset-generation discipline as the import-reject card (Codex rounds 7-10).
  const handleLoadMoreEvidence = async () => {
    if (!evidenceNextCursor || resetInFlightRef.current) return;
    const gen = fetchGenRef.current; // capture; a "load more" is not a reset
    const cursor = evidenceNextCursor;
    setLoadingMoreEvidence(true);
    try {
      const next = await shopifyRejectedEvidenceService.list({
        acknowledged: resolvedFilter,
        limit: 100,
        cursor,
      });
      if (gen !== fetchGenRef.current) return; // a reset started after us — discard
      setShopifyEvidence((prev) => {
        const seen = new Set(prev.map((r) => r.id));
        return [...prev, ...next.results.filter((r) => !seen.has(r.id))];
      });
      setShopifyEvidenceCount(next.total_count);
      setEvidenceNextCursor(next.next_cursor);
    } catch (err) {
      if (gen !== fetchGenRef.current) return;
      toast({
        title: "Failed to load more rejected evidence",
        description: (err as Error).message || "Try refreshing.",
        variant: "destructive",
      });
    } finally {
      setLoadingMoreEvidence(false);
    }
  };

  const handleAcknowledgeEvidence = async (row: ShopifyRejectedEvidence) => {
    setAckingEvidenceId(row.id);
    try {
      await shopifyRejectedEvidenceService.acknowledge(row.id);
      toast({
        title: "Rejected evidence acknowledged",
        description: `${row.resource_kind} ${row.external_id || "(no id)"} (${row.rejection_code}) — acknowledgment records review, not successful processing.`,
      });
      await fetchAll();
    } catch (err) {
      toast({
        title: "Failed to acknowledge",
        description: (err as Error).message,
        variant: "destructive",
      });
    } finally {
      setAckingEvidenceId(null);
    }
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

  const handleResolveReject = async (row: ImportRejectedRow) => {
    setResolvingRejectId(row.id);
    try {
      // The reject UI collects no note — never leak the projection-failure
      // `resolutionNote` onto an unrelated reject's audit trail (Codex P2).
      await importRejectedRowsService.resolve(row.id);
      toast({
        title: "Import rejection resolved",
        description: `${row.source_kind} row ${row.row_index} (${row.reason_code})`,
      });
      await fetchAll();
    } catch (err) {
      toast({
        title: "Failed to resolve",
        description: (err as Error).message,
        variant: "destructive",
      });
    } finally {
      setResolvingRejectId(null);
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
          subtitle="Projection failures and import rejections that need operator attention"
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
                {/* A5-PR3 (Codex round-4/5): fold the ALWAYS-UNRESOLVED import-reject
                    count into the page-level total + all-clear so this card can't say
                    "0 / all clear" while dropped import rows sit unresolved below —
                    and using the filter-independent count keeps the total correct
                    under the Resolved/All queue filters. A5-PR2b: the OPEN Shopify
                    rejected-evidence count folds in for the same reason. */}
                <div className="text-3xl font-semibold">
                  {summary.total_unresolved + unresolvedRejectsCount + openEvidenceCount}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {summary.total_unresolved + unresolvedRejectsCount + openEvidenceCount === 0
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
              {/* Codex round-6: the celebratory green all-clear only when there is
                  genuinely nothing anywhere. If projection failures are empty but
                  import rejects or rejected evidence are listed below, show a
                  projection-scoped note that points to them, so the page never
                  presents a contradictory "all clear" above an active rejection. */}
              {importRejectsCount === 0 && shopifyEvidenceCount === 0 ? (
                <>
                  <CheckCircle2 className="h-12 w-12 text-emerald-500" />
                  <h3 className="text-lg font-medium">No exceptions to show</h3>
                  <p className="text-sm text-muted-foreground">
                    {resolvedFilter === "false"
                      ? "All projections are running cleanly. When something fails, it'll show up here."
                      : "No failures match the current filters."}
                  </p>
                </>
              ) : (
                <>
                  <h3 className="text-lg font-medium">No projection failures</h3>
                  <p className="text-sm text-muted-foreground">
                    {resolvedFilter === "false"
                      ? "No projection failures — but there are rejected items below that need review."
                      : "No projection failures match the current filters. See the rejected items below."}
                  </p>
                </>
              )}
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

        {/* A5-PR3: durable per-row import rejections (settlement / bank CSV) —
            rows dropped at ingest with no event, so they have no
            ProjectionFailureLog. Surfaced here alongside projection failures. */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <AlertTriangle className="h-4 w-4" />
              Import rejections
              <Badge variant={importRejectsCount === 0 ? "outline" : "warning"}>
                {importRejectsCount}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {importRejects.length === 0 ? (
              <p className="px-4 py-6 text-sm text-muted-foreground">
                No dropped settlement/bank import rows.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="w-8" />
                    <th className="px-3 py-2 text-left">Source</th>
                    <th className="px-3 py-2 text-left">File</th>
                    <th className="px-3 py-2 text-right">Row</th>
                    <th className="px-3 py-2 text-left">Reason</th>
                    <th className="px-3 py-2 text-left">Detail</th>
                    <th className="px-3 py-2 text-right">Occurrences</th>
                    <th className="px-3 py-2 text-right">Last seen</th>
                    <th className="px-3 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {importRejects.map((r) => (
                    <Fragment key={r.id}>
                      <tr className="border-t hover:bg-muted/30">
                        <td className="px-2 py-2 align-top">
                          <button
                            onClick={() =>
                              setExpandedRejectId(expandedRejectId === r.id ? null : r.id)
                            }
                            className="inline-flex items-center text-muted-foreground hover:text-foreground"
                            aria-label={expandedRejectId === r.id ? "Collapse" : "Expand"}
                          >
                            {expandedRejectId === r.id ? (
                              <ChevronDown className="h-4 w-4" />
                            ) : (
                              <ChevronRight className="h-4 w-4" />
                            )}
                          </button>
                        </td>
                        <td className="px-3 py-2 align-top">
                          {r.source_kind}
                          {r.provider_code ? ` · ${r.provider_code}` : ""}
                        </td>
                        <td className="px-3 py-2 align-top font-mono text-xs">
                          {r.source_filename || "—"}
                        </td>
                        <td className="px-3 py-2 align-top text-right tabular-nums">
                          {r.row_index}
                        </td>
                        <td className="px-3 py-2 align-top">
                          <Badge variant="warning">{r.reason_code}</Badge>
                        </td>
                        <td className="px-3 py-2 align-top max-w-md">
                          <div className="line-clamp-2">{r.reason_message}</div>
                        </td>
                        <td className="px-3 py-2 align-top text-right tabular-nums">
                          {r.occurrence_count}
                        </td>
                        <td className="px-3 py-2 align-top text-right text-muted-foreground">
                          {timeAgo(r.last_seen_at)}
                        </td>
                        <td className="px-3 py-2 align-top text-right">
                          {r.resolved ? (
                            <Badge variant="outline">Resolved</Badge>
                          ) : isAdmin ? (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleResolveReject(r)}
                              disabled={resolvingRejectId === r.id}
                            >
                              {resolvingRejectId === r.id ? (
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
                      {expandedRejectId === r.id && (
                        <tr className="border-t bg-muted/20">
                          <td colSpan={9} className="space-y-3 p-4">
                            {r.reason_message && (
                              <div>
                                <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                                  Reason
                                </div>
                                <div className="text-sm">{r.reason_message}</div>
                              </div>
                            )}
                            <div>
                              <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                                Original row (preserved evidence)
                              </div>
                              <pre className="max-h-64 overflow-auto rounded border bg-background p-3 font-mono text-xs">
                                {JSON.stringify(r.raw_row, null, 2)}
                              </pre>
                            </div>
                            <div className="font-mono text-xs text-muted-foreground">
                              batch {r.import_batch_id} · first {r.first_seen_at} · last{" "}
                              {r.last_seen_at}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            )}
            {/* Keyset "Load more": shown while the server reports another page
                (next_cursor); disabled during ANY reset (reloading covers initial
                load, filter change, refresh, and resolve-triggered refetches) so a
                stale page can't be appended to a freshly reset list (Codex 7/8/9/10). */}
            {rejectNextCursor !== null && (
              <div className="border-t p-3 text-center">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleLoadMoreRejects}
                  disabled={loadingMoreRejects || reloading}
                >
                  {loadingMoreRejects ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    `Load more (${importRejects.length} of ${importRejectsCount})`
                  )}
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* A5-PR2b: rejected Shopify source evidence — authenticated ORDER/REFUND
            payloads from which no honest order/refund could be constructed. They
            never enter orders, events, journals or any financial reader; this
            queue is their only surface. Acknowledgment records review, NOT
            successful processing — only a corrected redelivery supersedes. */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <XCircle className="h-4 w-4" />
              Rejected Shopify payloads
              <Badge variant={shopifyEvidenceCount === 0 ? "outline" : "warning"}>
                {shopifyEvidenceCount}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {shopifyEvidence.length === 0 ? (
              <p className="px-4 py-6 text-sm text-muted-foreground">
                No rejected Shopify order/refund payloads.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="w-8" />
                    <th className="px-3 py-2 text-left">Kind</th>
                    <th className="px-3 py-2 text-left">Topic</th>
                    <th className="px-3 py-2 text-left">External id</th>
                    <th className="px-3 py-2 text-left">Code</th>
                    <th className="px-3 py-2 text-left">Detail</th>
                    <th className="px-3 py-2 text-right">Occurrences</th>
                    <th className="px-3 py-2 text-right">Last seen</th>
                    <th className="px-3 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {shopifyEvidence.map((r) => (
                    <Fragment key={r.id}>
                      <tr className="border-t hover:bg-muted/30">
                        <td className="px-2 py-2 align-top">
                          <button
                            onClick={() =>
                              setExpandedEvidenceId(expandedEvidenceId === r.id ? null : r.id)
                            }
                            className="inline-flex items-center text-muted-foreground hover:text-foreground"
                            aria-label={expandedEvidenceId === r.id ? "Collapse" : "Expand"}
                          >
                            {expandedEvidenceId === r.id ? (
                              <ChevronDown className="h-4 w-4" />
                            ) : (
                              <ChevronRight className="h-4 w-4" />
                            )}
                          </button>
                        </td>
                        <td className="px-3 py-2 align-top">
                          {r.resource_kind}
                          <span className="text-xs text-muted-foreground"> · {r.ingress_kind}</span>
                        </td>
                        <td className="px-3 py-2 align-top font-mono text-xs">{r.source_topic}</td>
                        <td className="px-3 py-2 align-top font-mono text-xs">
                          {r.external_id || "—"}
                        </td>
                        <td className="px-3 py-2 align-top">
                          <Badge variant="destructive">{r.rejection_code}</Badge>
                        </td>
                        <td className="px-3 py-2 align-top max-w-md">
                          <div className="line-clamp-2">{r.rejection_message}</div>
                        </td>
                        <td className="px-3 py-2 align-top text-right tabular-nums">
                          {r.occurrence_count}
                        </td>
                        <td className="px-3 py-2 align-top text-right text-muted-foreground">
                          {timeAgo(r.last_seen_at)}
                        </td>
                        <td className="px-3 py-2 align-top text-right">
                          {r.superseded_at ? (
                            <Badge variant="outline">Superseded</Badge>
                          ) : r.acknowledged ? (
                            <Badge variant="outline">Acknowledged</Badge>
                          ) : isAdmin ? (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleAcknowledgeEvidence(r)}
                              disabled={ackingEvidenceId === r.id}
                            >
                              {ackingEvidenceId === r.id ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                "Acknowledge"
                              )}
                            </Button>
                          ) : (
                            <span className="text-xs text-muted-foreground">Admin only</span>
                          )}
                        </td>
                      </tr>
                      {expandedEvidenceId === r.id && (
                        <tr className="border-t bg-muted/20">
                          <td colSpan={9} className="space-y-3 p-4">
                            <div>
                              <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                                Why it was rejected
                              </div>
                              <div className="text-sm">{r.rejection_message}</div>
                              {r.validation_errors.length > 0 && (
                                <ul className="mt-2 list-inside list-disc font-mono text-xs text-muted-foreground">
                                  {r.validation_errors.map((e, i) => (
                                    <li key={i}>
                                      [{e.code}] {e.field}: {e.message}
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </div>
                            <div>
                              <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                                Authenticated payload (preserved evidence)
                              </div>
                              {r.redacted_at ? (
                                <p className="text-xs text-muted-foreground">
                                  Payload redacted (GDPR) on {r.redacted_at} — hashes and rejection
                                  codes retained.
                                </p>
                              ) : r.parsed_payload !== null ? (
                                <pre className="max-h-64 overflow-auto rounded border bg-background p-3 font-mono text-xs">
                                  {JSON.stringify(r.parsed_payload, null, 2)}
                                </pre>
                              ) : (
                                <p className="text-xs text-muted-foreground">
                                  Body was not valid JSON — the exact authenticated bytes are
                                  preserved (base64, {r.raw_body_b64.length} chars).
                                </p>
                              )}
                            </div>
                            {r.superseded_at && (
                              <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-xs dark:border-emerald-900 dark:bg-emerald-950">
                                Superseded on {r.superseded_at} by canonical record{" "}
                                {r.superseded_target_public_id} — a corrected redelivery was processed.
                              </div>
                            )}
                            {r.acknowledged && r.acknowledgment_note && (
                              <div className="text-xs text-muted-foreground">
                                Acknowledgment note: {r.acknowledgment_note}
                              </div>
                            )}
                            <div className="font-mono text-xs text-muted-foreground">
                              {r.shop_domain} · payload {r.payload_hash.slice(0, 12)}… · delivery{" "}
                              {r.last_delivery_id || "—"} · first {r.first_seen_at} · last {r.last_seen_at}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            )}
            {evidenceNextCursor !== null && (
              <div className="border-t p-3 text-center">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleLoadMoreEvidence}
                  disabled={loadingMoreEvidence || reloading}
                >
                  {loadingMoreEvidence ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    `Load more (${shopifyEvidence.length} of ${shopifyEvidenceCount})`
                  )}
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
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
