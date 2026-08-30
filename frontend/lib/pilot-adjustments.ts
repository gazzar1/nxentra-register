// A5-PR4b — UI-only display vocabulary and helpers for pilot adjustments.
//
// The backend registry (backend/accounting/pilot_adjustments.py) is the sole
// authority for which source kinds are valid, their syntax, existence, company
// ownership, and referenceability. This module is a DISPLAY convenience only: it
// maps the currently supported kinds to human labels/hints, parses a stored
// "<kind>:<body>" source_document for presentation, builds the new-adjustment
// prefill URL, and points at a related application area. Unknown or malformed
// stored references must render safely as raw provenance — never crash a page.
// This is intentionally NOT a generic registry framework.

import type { PilotAdjustmentSourceKind } from "@/types/journal";

// The server-stamped source_module that marks a manual pilot adjustment.
export const PILOT_ADJUSTMENT_SOURCE_MODULE = "pilot_adjustment";

interface SourceKindDisplay {
  label: string;
  // A concise per-kind hint describing what the reference "body" is.
  referenceHint: string;
  // Area-level navigation only — never a claim of exact-row focus.
  relatedAreaHref: string;
}

// Display vocabulary for the seven currently supported source kinds. Keep the
// kind strings in lockstep with the backend registry; adding one here without
// the backend registering it affects presentation only (posting still refuses).
const SOURCE_KIND_DISPLAY: Record<PilotAdjustmentSourceKind, SourceKindDisplay> = {
  projection_failure: {
    label: "Projection failure",
    referenceHint: "BusinessEvent UUID of the failed projection event.",
    relatedAreaHref: "/finance/exceptions",
  },
  import_reject: {
    label: "Rejected or quarantined import row",
    referenceHint: "Public UUID of the rejected import row (ImportRejectedRow).",
    relatedAreaHref: "/finance/exceptions",
  },
  shopify_reject: {
    label: "Rejected Shopify payload",
    referenceHint: "Public UUID of the rejected Shopify payload (ShopifyRejectedEvidence).",
    relatedAreaHref: "/finance/exceptions",
  },
  shopify_order: {
    label: "Shopify order",
    referenceHint: "Numeric Shopify order ID.",
    relatedAreaHref: "/shopify/orders",
  },
  shopify_refund: {
    label: "Shopify refund",
    referenceHint: "Numeric Shopify refund ID.",
    relatedAreaHref: "/finance/reconciliation",
  },
  settlement_event: {
    label: "Settlement event",
    referenceHint: "BusinessEvent UUID of the settlement event.",
    relatedAreaHref: "/finance/reconciliation#stage-2",
  },
  bank_line: {
    label: "Bank statement line",
    referenceHint: "Public UUID of the bank statement line (BankStatementLine).",
    relatedAreaHref: "/accounting/bank-reconciliation",
  },
};

// Stable display order for the source-kind picker.
export const PILOT_ADJUSTMENT_SOURCE_KINDS: PilotAdjustmentSourceKind[] = [
  "projection_failure",
  "import_reject",
  "shopify_reject",
  "shopify_order",
  "shopify_refund",
  "settlement_event",
  "bank_line",
];

export function isPilotAdjustmentSourceKind(
  value: unknown,
): value is PilotAdjustmentSourceKind {
  return (
    typeof value === "string" &&
    Object.prototype.hasOwnProperty.call(SOURCE_KIND_DISPLAY, value)
  );
}

// Human label for a kind; falls back to the raw string for an unknown kind so a
// malformed stored value still renders (never crashes).
export function pilotAdjustmentKindLabel(kind: string): string {
  return isPilotAdjustmentSourceKind(kind) ? SOURCE_KIND_DISPLAY[kind].label : kind;
}

export function pilotAdjustmentReferenceHint(kind: string): string {
  return isPilotAdjustmentSourceKind(kind) ? SOURCE_KIND_DISPLAY[kind].referenceHint : "";
}

export interface ParsedSourceDocument {
  kind: PilotAdjustmentSourceKind;
  body: string;
}

// Parse a canonical "<kind>:<body>" source_document for DISPLAY. Returns null for
// anything that is not a recognised "<known-kind>:<non-empty-body>" — callers
// must fall back to rendering the raw provenance safely.
export function parseSourceDocument(
  sourceDocument: string | null | undefined,
): ParsedSourceDocument | null {
  if (typeof sourceDocument !== "string") return null;
  const sep = sourceDocument.indexOf(":");
  if (sep <= 0) return null;
  const kind = sourceDocument.slice(0, sep);
  const body = sourceDocument.slice(sep + 1);
  if (!body) return null;
  if (!isPilotAdjustmentSourceKind(kind)) return null;
  return { kind, body };
}

// Related application area for a source kind. Always area-level navigation with
// the label "Open related area" (never "View exact source"). Null for an unknown
// kind.
export function relatedAreaFor(
  kind: string,
): { href: string; label: string } | null {
  if (!isPilotAdjustmentSourceKind(kind)) return null;
  return { href: SOURCE_KIND_DISPLAY[kind].relatedAreaHref, label: "Open related area" };
}

// Build the prefill URL for the new-adjustment form. Only the two whitelisted
// query params are emitted; raw source_module/source_document are never used.
export function buildNewAdjustmentHref(
  kind: PilotAdjustmentSourceKind,
  reference: string,
): string {
  const params = new URLSearchParams({
    adjustment_source_kind: kind,
    adjustment_source_reference: reference,
  });
  return `/accounting/journal-entries/new?${params.toString()}`;
}
