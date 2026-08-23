/**
 * A85 (2026-05-26): typed client for the settlement CSV preview + commit
 * + period-override flow.
 *
 * Backs the JEPreviewModal component. Three operations:
 *   1. previewSettlementImport — POST CSV, get plan (no commit).
 *   2. commitSettlementImport — POST CSV again, this time committing.
 *      Optionally carries period override + reason.
 *   3. listOpenPeriods — get OPEN FiscalPeriods for the period-picker
 *      dropdown when the operator wants to override.
 */

import apiClient from "@/lib/api-client";

// =============================================================================
// Types — mirror backend settlement_imports.py preview shape
// =============================================================================

export type SettlementProvider = "paymob" | "bosta";

export interface PreviewedBatch {
  batch_id: string;
  payout_date: string;
  gross: string;
  fees: string;
  net: string;
  uncollected: string;
  line_count: number;
  resolved_period: {
    resolved: boolean;
    fiscal_year: number | null;
    period: number | null;
    period_name: string | null;
    status: "OPEN" | "CLOSED" | null;
    warning: string | null;
  };
  already_imported: boolean;
  will_create_journal_entry: boolean;
  unknown_order_ids: string[];
  warnings: string[];
}

export interface PreviewedPeriod {
  fiscal_year: number;
  period: number;
  period_name: string;
  status: "OPEN" | "CLOSED";
  journal_entries: number;
}

// A5-PR3b: a source row the parser dropped/flagged — durable ImportRejectedRow
// evidence on commit, surfaced here so a partial import is never presented as
// wholly successful.
export interface SettlementRejectedRow {
  row_index: number;
  reason_code: string;
  reason_message: string;
  status?: string;
}

export interface SettlementImportPreview {
  provider: SettlementProvider;
  filename: string;
  batches: PreviewedBatch[];
  summary: {
    total_batches: number;
    total_journal_entries_to_create: number;
    periods_affected: PreviewedPeriod[];
    blockers: string[];
    dry_run_safe: boolean;
    total_gross: string;
    total_fees: string;
    total_net: string;
    rejected_row_count: number;
    rejected_rows: SettlementRejectedRow[];
  };
}

export interface CommitParams {
  file: File;
  provider: SettlementProvider;
  paymentMethod?: string;
  // Optional period override (operator action)
  periodOverride?: number;
  fiscalYearOverride?: number;
  overrideReason?: string;
}

export interface CommitResult {
  provider: string;
  filename: string;
  batches: Array<{
    event_id?: string | null;
    batch_id: string;
    provider: string;
    gross: string;
    fees: string;
    net: string;
    uncollected: string;
    line_count: number;
    deduplicated: boolean;
    unknown_order_ids: string[];
  }>;
  batch_count: number;
  // A5-PR3b: this upload's durable evidence (grouped by import_batch_id; listed
  // under Finance → Exceptions). Rejected rows were EXCLUDED from posting;
  // review rows (orphan order ids) POSTED and need review (Codex round-7).
  import_batch_id: string;
  rejected_row_count: number;
  rejected_rows: SettlementRejectedRow[];
  review_row_count: number;
  review_rows: SettlementRejectedRow[];
}

export interface OpenFiscalPeriod {
  fiscal_year: number;
  period: number;
  period_name: string;
  start_date: string;
  end_date: string;
  status: "OPEN" | "CLOSED";
}

// =============================================================================
// Service
// =============================================================================

const PREVIEW_URL = "/accounting/settlements/import/preview/";
const COMMIT_URL = "/accounting/settlements/import/";
const PERIODS_URL = "/reports/periods/";

export const settlementImportService = {
  async preview(file: File, provider: SettlementProvider): Promise<SettlementImportPreview> {
    const form = new FormData();
    form.append("file", file);
    form.append("provider", provider);
    const { data } = await apiClient.post(PREVIEW_URL, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  },

  async commit(params: CommitParams): Promise<CommitResult> {
    const form = new FormData();
    form.append("file", params.file);
    form.append("provider", params.provider);
    if (params.paymentMethod) form.append("payment_method", params.paymentMethod);
    if (params.periodOverride && params.fiscalYearOverride && params.overrideReason) {
      form.append("period_override", String(params.periodOverride));
      form.append("fiscal_year_override", String(params.fiscalYearOverride));
      form.append("override_reason", params.overrideReason);
    }
    const { data } = await apiClient.post(COMMIT_URL, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  },

  async listOpenPeriods(): Promise<OpenFiscalPeriod[]> {
    const { data } = await apiClient.get(PERIODS_URL);
    const periods: OpenFiscalPeriod[] = Array.isArray(data) ? data : data.results || [];
    return periods.filter((p) => p.status === "OPEN");
  },
};
