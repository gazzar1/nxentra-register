/**
 * A85 chunk 4 (2026-05-26): pre-flight modal for settlement-CSV import.
 *
 * Renders the preview returned by settlementImportService.preview(),
 * lets the operator (a) cancel, (b) post with the default period, or
 * (c) post with a period override IF they have the permission AND
 * supply a reason of >=10 chars.
 *
 * Per docs/finance_event_first_policy.md §8 (loud failures, not silent)
 * and ENGINEERING_PROTOCOL.md §1.5 (auditability beats convenience):
 * the operator sees cause-and-effect before commit; overrides are
 * audit-logged via PeriodOverrideAudit at the API layer.
 */

import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  Info,
  Loader2,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type {
  OpenFiscalPeriod,
  PreviewedBatch,
  SettlementImportPreview,
} from "@/services/settlement-import.service";

// =============================================================================
// Props
// =============================================================================

export interface JEPreviewModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  preview: SettlementImportPreview | null;
  /** Operator's existing permission set; we look for accounting.je.override_period */
  userPermissions: Set<string>;
  /** OPEN fiscal periods, for the override picker */
  openPeriods: OpenFiscalPeriod[];
  /** Called when operator clicks Post All. Pass override fields if applicable. */
  onConfirm: (override?: {
    period: number;
    fiscalYear: number;
    reason: string;
  }) => Promise<void> | void;
  /** Loading flag for the Post All button. */
  isPosting?: boolean;
}

// =============================================================================
// Component
// =============================================================================

export function JEPreviewModal({
  open,
  onOpenChange,
  preview,
  userPermissions,
  openPeriods,
  onConfirm,
  isPosting = false,
}: JEPreviewModalProps) {
  const canOverride = userPermissions.has("accounting.je.override_period");

  const [expandedBatch, setExpandedBatch] = useState<string | null>(null);
  const [showOverride, setShowOverride] = useState(false);
  const [overridePeriodKey, setOverridePeriodKey] = useState<string>("");
  const [overrideReason, setOverrideReason] = useState<string>("");

  // Whether the auto-resolved plan is safe to post as-is (no closed periods,
  // no missing periods). If false AND the user can override, force-show
  // the override section so they don't get stuck.
  const hasBlockers = (preview?.summary.blockers.length ?? 0) > 0;
  const overrideSectionVisible = showOverride || (hasBlockers && canOverride);

  const reasonValid = overrideReason.trim().length >= 10;
  const overridePeriodSelected = overridePeriodKey !== "";

  const overrideReady = overrideSectionVisible
    ? overridePeriodSelected && reasonValid
    : true;

  const canPost = useMemo(() => {
    if (!preview || preview.summary.total_journal_entries_to_create === 0) return false;
    if (overrideSectionVisible) return overrideReady;
    return !hasBlockers;
  }, [preview, hasBlockers, overrideSectionVisible, overrideReady]);

  const handleConfirm = async () => {
    if (!preview) return;
    if (overrideSectionVisible && overrideReady) {
      const [yearStr, periodStr] = overridePeriodKey.split(":");
      await onConfirm({
        period: parseInt(periodStr, 10),
        fiscalYear: parseInt(yearStr, 10),
        reason: overrideReason.trim(),
      });
    } else {
      await onConfirm();
    }
  };

  if (!preview) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5" />
            Review {preview.provider} settlement import
          </DialogTitle>
          <DialogDescription>
            {preview.filename}
            {" — "}
            {preview.summary.total_batches} batch
            {preview.summary.total_batches !== 1 ? "es" : ""},{" "}
            <strong>
              {preview.summary.total_journal_entries_to_create} journal entr
              {preview.summary.total_journal_entries_to_create !== 1 ? "ies" : "y"}
            </strong>{" "}
            will be created
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-1">
          {/* Summary totals */}
          <SummaryTotals preview={preview} />

          {/* Periods affected */}
          <PeriodsAffected preview={preview} />

          {/* Blockers */}
          {hasBlockers && (
            <div className="rounded border border-destructive bg-destructive/10 p-3 text-sm">
              <div className="mb-2 flex items-center gap-2 font-medium text-destructive">
                <XCircle className="h-4 w-4" />
                Blockers ({preview.summary.blockers.length})
              </div>
              <ul className="space-y-1 pl-6 text-destructive">
                {preview.summary.blockers.map((b, i) => (
                  <li key={i} className="list-disc">
                    {b}
                  </li>
                ))}
              </ul>
              {canOverride && (
                <p className="mt-2 text-xs text-muted-foreground">
                  You have the override permission — see below to pick a
                  different period.
                </p>
              )}
              {!canOverride && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Resolve the blocker (reopen the period, or ask an admin to
                  override) before importing.
                </p>
              )}
            </div>
          )}

          {/* Per-batch detail */}
          <BatchTable
            batches={preview.batches}
            expandedBatch={expandedBatch}
            onToggle={(id) =>
              setExpandedBatch(expandedBatch === id ? null : id)
            }
          />

          {/* Override toggle */}
          {canOverride && !hasBlockers && (
            <div>
              <button
                type="button"
                onClick={() => setShowOverride(!showOverride)}
                className="text-sm text-blue-600 hover:underline dark:text-blue-400"
              >
                {showOverride
                  ? "Cancel period override"
                  : "Override the default posting period…"}
              </button>
            </div>
          )}

          {/* Override picker + reason */}
          {overrideSectionVisible && (
            <OverrideSection
              openPeriods={openPeriods}
              periodKey={overridePeriodKey}
              onPeriodChange={setOverridePeriodKey}
              reason={overrideReason}
              onReasonChange={setOverrideReason}
              reasonValid={reasonValid}
            />
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPosting}
          >
            Cancel
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={!canPost || isPosting}
          >
            {isPosting ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Posting…
              </>
            ) : overrideSectionVisible ? (
              "Post all (with override)"
            ) : (
              `Post ${preview.summary.total_journal_entries_to_create} JEs`
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// =============================================================================
// Sub-components
// =============================================================================

function SummaryTotals({ preview }: { preview: SettlementImportPreview }) {
  return (
    <div className="grid grid-cols-3 gap-3 rounded border bg-muted/30 p-3 text-sm">
      <div>
        <div className="text-xs uppercase text-muted-foreground">Gross</div>
        <div className="text-lg font-semibold tabular-nums">
          {preview.summary.total_gross}
        </div>
      </div>
      <div>
        <div className="text-xs uppercase text-muted-foreground">Fees</div>
        <div className="text-lg font-semibold tabular-nums">
          {preview.summary.total_fees}
        </div>
      </div>
      <div>
        <div className="text-xs uppercase text-muted-foreground">Net</div>
        <div className="text-lg font-semibold tabular-nums">
          {preview.summary.total_net}
        </div>
      </div>
    </div>
  );
}

function PeriodsAffected({
  preview,
}: {
  preview: SettlementImportPreview;
}) {
  if (preview.summary.periods_affected.length === 0) return null;
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
        Periods affected
      </div>
      <div className="flex flex-wrap gap-2">
        {preview.summary.periods_affected.map((p) => (
          <Badge
            key={`${p.fiscal_year}-${p.period}`}
            variant={p.status === "OPEN" ? "secondary" : "destructive"}
            className="inline-flex items-center gap-1"
          >
            {p.status === "OPEN" ? (
              <CheckCircle2 className="h-3 w-3" />
            ) : (
              <AlertTriangle className="h-3 w-3" />
            )}
            {p.period_name}
            <span className="opacity-70">({p.journal_entries} JE)</span>
          </Badge>
        ))}
      </div>
    </div>
  );
}

function BatchTable({
  batches,
  expandedBatch,
  onToggle,
}: {
  batches: PreviewedBatch[];
  expandedBatch: string | null;
  onToggle: (id: string) => void;
}) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
        Batches ({batches.length})
      </div>
      <div className="overflow-hidden rounded border text-sm">
        <table className="w-full">
          <thead className="bg-muted/50">
            <tr>
              <th className="w-8"></th>
              <th className="px-2 py-1 text-left">Batch</th>
              <th className="px-2 py-1 text-left">Date</th>
              <th className="px-2 py-1 text-right">Net</th>
              <th className="px-2 py-1 text-left">Period</th>
              <th className="px-2 py-1 text-left">Status</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => {
              const isOpen = expandedBatch === b.batch_id;
              return (
                <>
                  <tr
                    key={b.batch_id}
                    className="cursor-pointer border-t hover:bg-muted/30"
                    onClick={() => onToggle(b.batch_id)}
                  >
                    <td className="px-2 py-1">
                      {isOpen ? (
                        <ChevronDown className="h-3 w-3" />
                      ) : (
                        <ChevronRight className="h-3 w-3" />
                      )}
                    </td>
                    <td className="px-2 py-1 font-mono text-xs">{b.batch_id}</td>
                    <td className="px-2 py-1">{b.payout_date}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{b.net}</td>
                    <td className="px-2 py-1">
                      {b.resolved_period.resolved ? (
                        `${b.resolved_period.period_name}`
                      ) : (
                        <span className="text-destructive">unresolved</span>
                      )}
                    </td>
                    <td className="px-2 py-1">
                      {b.already_imported ? (
                        <Badge variant="outline">Duplicate</Badge>
                      ) : b.warnings.length ? (
                        <Badge variant="warning">Warnings</Badge>
                      ) : (
                        <Badge variant="secondary">Will post</Badge>
                      )}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-t bg-muted/20 text-xs">
                      <td colSpan={6} className="p-3">
                        <div className="space-y-2">
                          <div>
                            <strong>Gross:</strong> {b.gross} —{" "}
                            <strong>Fees:</strong> {b.fees} —{" "}
                            <strong>Net:</strong> {b.net} —{" "}
                            <strong>Lines:</strong> {b.line_count}
                          </div>
                          {b.warnings.length > 0 && (
                            <ul className="space-y-1 pl-4 text-amber-600 dark:text-amber-400">
                              {b.warnings.map((w, i) => (
                                <li key={i} className="list-disc">
                                  {w}
                                </li>
                              ))}
                            </ul>
                          )}
                          {b.unknown_order_ids.length > 0 && (
                            <div className="text-muted-foreground">
                              Unknown order IDs:{" "}
                              {b.unknown_order_ids.slice(0, 8).join(", ")}
                              {b.unknown_order_ids.length > 8 ? "…" : ""}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OverrideSection({
  openPeriods,
  periodKey,
  onPeriodChange,
  reason,
  onReasonChange,
  reasonValid,
}: {
  openPeriods: OpenFiscalPeriod[];
  periodKey: string;
  onPeriodChange: (v: string) => void;
  reason: string;
  onReasonChange: (v: string) => void;
  reasonValid: boolean;
}) {
  return (
    <div className="rounded border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950">
      <div className="mb-2 flex items-center gap-2 font-medium">
        <Info className="h-4 w-4" />
        Override default posting period
      </div>
      <p className="mb-3 text-xs text-muted-foreground">
        Powerful action — every override is audit-logged
        (PeriodOverrideAudit). Reason will be visible in the
        /audit/period-overrides report.
      </p>
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">
            Target period
          </label>
          <select
            value={periodKey}
            onChange={(e) => onPeriodChange(e.target.value)}
            className="w-full rounded border bg-background px-2 py-1 text-sm"
          >
            <option value="">— Pick an open period —</option>
            {openPeriods.map((p) => (
              <option
                key={`${p.fiscal_year}-${p.period}`}
                value={`${p.fiscal_year}:${p.period}`}
              >
                Period {p.period} / {p.fiscal_year} ({p.period_name})
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">
            Reason (≥10 chars, required)
          </label>
          <textarea
            value={reason}
            onChange={(e) => onReasonChange(e.target.value)}
            rows={2}
            placeholder="e.g., April period closed for audit review; posting to May per CFO approval."
            className={`w-full rounded border bg-background p-2 text-sm ${
              reason.length > 0 && !reasonValid ? "border-destructive" : ""
            }`}
          />
          <div className="mt-1 text-xs text-muted-foreground">
            {reason.length}/10 chars min
            {reason.length > 0 && !reasonValid && (
              <span className="text-destructive"> — too short</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
