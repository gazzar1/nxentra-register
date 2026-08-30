import { GetServerSideProps } from "next";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useState } from "react";
import {
  ArrowLeft,
  Send,
  Undo2,
  Trash2,
  Pencil,
  Printer,
  CheckCircle2,
  Copy,
  AlertTriangle,
} from "lucide-react";
import Link from "next/link";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, LoadingSpinner, StatusBadge, ConfirmDialog } from "@/components/common";
import {
  useJournalEntry,
  usePostJournalEntry,
  useReverseJournalEntry,
  useDeleteJournalEntry,
} from "@/queries/useJournalEntries";
import { useAuth } from "@/contexts/AuthContext";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import {
  canPostJournalEntry,
  canReverseJournalEntry,
  canEditJournalEntry,
  canDeleteJournalEntry,
} from "@/types/journal";
import type { PilotAdjustmentSourceKind, JournalEntryReversePayload } from "@/types/journal";
import { isConstrainedPilot } from "@/lib/constrained-pilot";
import {
  parseSourceDocument,
  pilotAdjustmentKindLabel,
  pilotAdjustmentReferenceHint,
  relatedAreaFor,
  PILOT_ADJUSTMENT_SOURCE_MODULE,
  PILOT_ADJUSTMENT_SOURCE_KINDS,
} from "@/lib/pilot-adjustments";

export default function JournalEntryDetailPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { company } = useAuth();
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();
  const { toast } = useToast();
  const id = Number(router.query.id);

  const { data: entry, isLoading } = useJournalEntry(id);
  const postEntry = usePostJournalEntry();
  const reverseEntry = useReverseJournalEntry();
  const deleteEntry = useDeleteJournalEntry();

  const [showPostConfirm, setShowPostConfirm] = useState(false);
  const [showReverseConfirm, setShowReverseConfirm] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // A5-PR4b: active-pilot reversal input state.
  const [showReverseDialog, setShowReverseDialog] = useState(false);
  const [reverseReason, setReverseReason] = useState("");
  const [reverseSourceKind, setReverseSourceKind] = useState<PilotAdjustmentSourceKind | "">("");
  const [reverseSourceRef, setReverseSourceRef] = useState("");
  const [reverseError, setReverseError] = useState<string | null>(null);

  const functionalCurrency = company?.functional_currency || company?.default_currency || "USD";
  const isForeignCurrency = entry?.currency && entry.currency !== functionalCurrency;

  // A5-PR4b: pilot-adjustment traceability + post-readiness (identify ONLY by
  // source_module — never by JournalEntry.kind or memo text).
  const pilotActive = isConstrainedPilot(company?.pilot_profile);
  const isPilotAdjustment = entry?.source_module === PILOT_ADJUSTMENT_SOURCE_MODULE;
  const parsedSource = isPilotAdjustment ? parseSourceDocument(entry?.source_document) : null;
  const systemProvenance =
    entry && entry.source_module && !isPilotAdjustment
      ? { module: entry.source_module, document: entry.source_document }
      : null;
  const memoLen = (entry?.memo || "").trim().length;
  const localTraceReady =
    isPilotAdjustment && parsedSource !== null && memoLen >= 10 && memoLen <= 180;
  // The reversal inherits provenance only when the original already carries a
  // pilot-adjustment stamp; otherwise a fresh typed source is required.
  const reversalInheritsSource =
    entry?.source_module === PILOT_ADJUSTMENT_SOURCE_MODULE && Boolean(entry?.source_document);

  const copyReference = (text: string) => {
    navigator.clipboard?.writeText(text).then(
      () => toast({ title: "Reference copied", variant: "success" }),
      () => toast({ title: "Could not copy reference", variant: "destructive" })
    );
  };

  const resetReverseFields = () => {
    setReverseReason("");
    setReverseSourceKind("");
    setReverseSourceRef("");
    setReverseError(null);
  };

  const handlePost = async () => {
    try {
      await postEntry.mutateAsync(id);
      toast({
        title: t("messages.success"),
        description: t("accounting:messages.postSuccess"),
        variant: "success",
      });
      setShowPostConfirm(false);
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  // Profile NONE: unchanged behaviour — a simple confirmation, empty-body reverse.
  const handleReverse = async () => {
    try {
      await reverseEntry.mutateAsync({ id });
      toast({
        title: t("messages.success"),
        description: t("accounting:messages.reverseSuccess"),
        variant: "success",
      });
      setShowReverseConfirm(false);
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  // Active pilot: reversal requires its own reason (10–180); a fresh source is
  // required only when the original lacks pilot-adjustment provenance. The dialog
  // stays open on failure and clears its fields on success/cancel.
  const handleReversePilot = async () => {
    const reason = reverseReason.trim();
    if (reason.length < 10 || reason.length > 180) {
      setReverseError("The reversal reason must be 10–180 characters.");
      return;
    }
    const payload: JournalEntryReversePayload = { reason };
    if (!reversalInheritsSource) {
      const ref = reverseSourceRef.trim();
      if (!reverseSourceKind || !ref) {
        setReverseError("Choose a source type and reference for the reversal.");
        return;
      }
      payload.adjustment_source_kind = reverseSourceKind;
      payload.adjustment_source_reference = ref;
    }
    setReverseError(null);
    try {
      await reverseEntry.mutateAsync({ id, payload });
      toast({
        title: t("messages.success"),
        description: t("accounting:messages.reverseSuccess"),
        variant: "success",
      });
      setShowReverseDialog(false);
      resetReverseFields();
    } catch (error) {
      // Keep the dialog open with the operator's input intact.
      setReverseError(getErrorMessage(error));
    }
  };

  const handleDelete = async () => {
    try {
      await deleteEntry.mutateAsync(id);
      toast({
        title: t("messages.success"),
        description: t("messages.deleted"),
        variant: "success",
      });
      router.push("/accounting/journal-entries");
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <div className="flex justify-center py-20">
          <LoadingSpinner size="lg" />
        </div>
      </AppLayout>
    );
  }

  if (!entry) {
    return (
      <AppLayout>
        <div className="text-center py-20">
          <p className="text-muted-foreground">{t("messages.noData")}</p>
          <Link href="/accounting/journal-entries">
            <Button variant="outline" className="mt-4">
              <ArrowLeft className="me-2 h-4 w-4" />
              {t("actions.back")}
            </Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={entry.entry_number ? `#${entry.entry_number}` : `#${entry.id}`}
          subtitle={entry.memo || t("accounting:journalEntries.entryDetails")}
          actions={
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                onClick={() => window.open(`/accounting/journal-entries/${id}/print`, '_blank')}
              >
                <Printer className="me-2 h-4 w-4" />
                {t("actions.print")}
              </Button>
              {canEditJournalEntry(entry) && (
                <Button
                  variant="outline"
                  onClick={() => router.push(`/accounting/journal-entries/${id}/edit`)}
                >
                  <Pencil className="me-2 h-4 w-4" />
                  {t("actions.edit")}
                </Button>
              )}
              {/* A5-PR4b: under the active pilot, only offer Post when the local
                  trace is ready — never a normal-looking Post guaranteed to fail. */}
              {canPostJournalEntry(entry) &&
                (!pilotActive || (isPilotAdjustment && localTraceReady)) && (
                  <Button onClick={() => setShowPostConfirm(true)}>
                    <Send className="me-2 h-4 w-4" />
                    {t("accounting:journalEntries.postEntry")}
                  </Button>
                )}
              {canReverseJournalEntry(entry) && (
                <Button
                  variant="outline"
                  onClick={() =>
                    pilotActive ? setShowReverseDialog(true) : setShowReverseConfirm(true)
                  }
                >
                  <Undo2 className="me-2 h-4 w-4" />
                  {t("accounting:journalEntries.reverseEntry")}
                </Button>
              )}
              {canDeleteJournalEntry(entry) && (
                <Button variant="destructive" onClick={() => setShowDeleteConfirm(true)}>
                  <Trash2 className="me-2 h-4 w-4" />
                  {t("actions.delete")}
                </Button>
              )}
            </div>
          }
        />

        {/* Reversal cross-link — original ↔ reversal, by user-facing entry number */}
        {entry.reverses_entry && (
          <div className="flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm">
            <Undo2 className="h-4 w-4 shrink-0 text-amber-600" />
            <span>
              {t("accounting:journalEntries.reversalOf", "This entry reverses")}{" "}
              <Link
                href={`/accounting/journal-entries/${entry.reverses_entry}`}
                className="font-mono font-medium text-primary hover:underline"
              >
                {entry.reverses_entry_number || `#${entry.reverses_entry}`}
              </Link>
              .
            </span>
          </div>
        )}
        {entry.reversed_by_entry && (
          <div className="flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm">
            <Undo2 className="h-4 w-4 shrink-0 text-amber-600" />
            <span>
              {t("accounting:journalEntries.reversedBy", "This entry was reversed by")}{" "}
              <Link
                href={`/accounting/journal-entries/${entry.reversed_by_entry}`}
                className="font-mono font-medium text-primary hover:underline"
              >
                {entry.reversed_by_entry_number || `#${entry.reversed_by_entry}`}
              </Link>
              .
            </span>
          </div>
        )}

        {/* A5-PR4b: active-pilot draft that is not yet trace-ready */}
        {pilotActive &&
          entry.status === "DRAFT" &&
          !(isPilotAdjustment && localTraceReady) &&
          (systemProvenance ? (
            <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <span>
                This draft belongs to an automated process (system-owned provenance).
                It is not a manual pilot adjustment and cannot be relabelled or posted
                through the manual form.
              </span>
            </div>
          ) : (
            <div className="flex flex-col gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm">
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <span>
                  Not ready to post. Under the pilot, this entry must carry a source
                  reference and a 10–180 character reason before it can be posted. The
                  server makes the final decision — a locally complete entry may still be
                  refused if the source is missing or belongs to another company.
                </span>
              </div>
              <div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => router.push(`/accounting/journal-entries/${id}/edit`)}
                >
                  <Pencil className="me-2 h-4 w-4" />
                  Edit adjustment evidence
                </Button>
              </div>
            </div>
          ))}

        {/* A5-PR4b: durable pilot-adjustment traceability card */}
        {isPilotAdjustment && (
          <Card className="border-primary/30 bg-primary/5">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Badge variant="info">Pilot adjustment</Badge>
                Adjustment traceability
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div>
                <dt className="text-xs text-muted-foreground">
                  {entry.reverses_entry ? "Reversal narrative" : "Adjustment reason"}
                </dt>
                <dd className="font-medium">{entry.memo || "—"}</dd>
              </div>
              {parsedSource ? (
                <>
                  <div>
                    <dt className="text-xs text-muted-foreground">Source</dt>
                    <dd className="font-medium">{pilotAdjustmentKindLabel(parsedSource.kind)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Source reference</dt>
                    <dd className="flex items-center gap-2">
                      <code className="font-mono text-xs break-all">{entry.source_document}</code>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => copyReference(entry.source_document)}
                        title="Copy reference"
                      >
                        <Copy className="h-4 w-4" />
                        <span className="sr-only">Copy reference</span>
                      </Button>
                    </dd>
                  </div>
                  {relatedAreaFor(parsedSource.kind) && (
                    <div>
                      <Link
                        href={relatedAreaFor(parsedSource.kind)!.href}
                        className="text-sm text-primary hover:underline"
                      >
                        {relatedAreaFor(parsedSource.kind)!.label} →
                      </Link>
                    </div>
                  )}
                </>
              ) : (
                <div>
                  <dt className="text-xs text-muted-foreground">Source reference (raw)</dt>
                  <dd className="flex items-center gap-2">
                    <code className="font-mono text-xs break-all">
                      {entry.source_document || "—"}
                    </code>
                    {entry.source_document && (
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => copyReference(entry.source_document)}
                        title="Copy reference"
                      >
                        <Copy className="h-4 w-4" />
                        <span className="sr-only">Copy reference</span>
                      </Button>
                    )}
                  </dd>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* A5-PR4b: system provenance (read-only) — surfaced under the active pilot
            so operators can tell an automated entry from a manual pilot adjustment. */}
        {pilotActive && systemProvenance && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">System provenance</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="text-xs text-muted-foreground">
                This entry was produced by an automated process. It is not a manual
                pilot adjustment.
              </p>
              <div>
                <dt className="text-xs text-muted-foreground">Module</dt>
                <dd className="font-mono break-all">{systemProvenance.module}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Document</dt>
                <dd className="font-mono break-all">{systemProvenance.document || "—"}</dd>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Entry Info */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              {t("accounting:journalEntries.entryDetails")}
              <StatusBadge status={entry.status} />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <div>
                <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.date")}</dt>
                <dd className="font-medium">{formatDate(entry.date)}</dd>
              </div>
              <div>
                <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.kind")}</dt>
                <dd className="font-medium">{t(`accounting:entryKinds.${entry.kind}`)}</dd>
              </div>
              <div>
                <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.currency")}</dt>
                <dd className="font-medium">
                  {entry.currency || "-"}
                  {isForeignCurrency && (
                    <span className="ms-2 inline-flex items-center rounded-full bg-blue-500/10 px-2 py-0.5 text-xs text-blue-500">
                      Foreign
                    </span>
                  )}
                </dd>
              </div>
              {isForeignCurrency && (
                <div>
                  <dt className="text-sm text-muted-foreground">Exchange Rate</dt>
                  <dd className="font-medium font-mono">
                    1 {entry.currency} = {parseFloat(entry.exchange_rate).toFixed(6)} {functionalCurrency}
                  </dd>
                </div>
              )}
              <div>
                <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.status")}</dt>
                <dd><StatusBadge status={entry.status} /></dd>
              </div>
              {entry.memo && (
                <div className="sm:col-span-2">
                  <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.memo")}</dt>
                  <dd className="font-medium">{entry.memo}</dd>
                </div>
              )}
              {entry.posted_at && (
                <div>
                  <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.postedAt")}</dt>
                  <dd className="font-medium">{new Date(entry.posted_at).toLocaleString()}</dd>
                </div>
              )}
              {entry.reversed_at && (
                <div>
                  <dt className="text-sm text-muted-foreground">{t("accounting:journalEntry.reversedAt")}</dt>
                  <dd className="font-medium">{new Date(entry.reversed_at).toLocaleString()}</dd>
                </div>
              )}
            </dl>
          </CardContent>
        </Card>

        {/* Lines Table */}
        <Card>
          <CardHeader>
            <CardTitle>{t("accounting:journalEntries.lines")}</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16">{t("accounting:journalLine.lineNo")}</TableHead>
                  <TableHead>{t("accounting:journalLine.account")}</TableHead>
                  <TableHead>{t("accounting:journalLine.description")}</TableHead>
                  {isForeignCurrency && (
                    <TableHead className="text-end">
                      Foreign ({entry.currency})
                    </TableHead>
                  )}
                  <TableHead className="text-end">
                    {t("accounting:journalLine.debit")}
                    {isForeignCurrency && <span className="text-xs text-muted-foreground ms-1">({functionalCurrency})</span>}
                  </TableHead>
                  <TableHead className="text-end">
                    {t("accounting:journalLine.credit")}
                    {isForeignCurrency && <span className="text-xs text-muted-foreground ms-1">({functionalCurrency})</span>}
                  </TableHead>
                  <TableHead className="w-16 text-center">Reconciled</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entry.lines.map((line, idx) => (
                  <TableRow key={line.public_id || idx}>
                    <TableCell className="font-mono ltr-code">{line.line_no}</TableCell>
                    <TableCell>
                      <span className="font-mono ltr-code text-sm">{line.account_code}</span>
                      {line.account_name && (
                        <span className="ms-2 text-muted-foreground">{line.account_name}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {line.description || "-"}
                    </TableCell>
                    {isForeignCurrency && (
                      <TableCell className="text-end ltr-number font-medium text-blue-500">
                        {line.amount_currency
                          ? formatCurrency(
                              String(Math.abs(parseFloat(line.amount_currency))),
                              entry.currency
                            )
                          : "-"}
                      </TableCell>
                    )}
                    <TableCell className="text-end ltr-number font-medium">
                      {parseFloat(line.debit) > 0 ? formatCurrency(line.debit, functionalCurrency) : "-"}
                    </TableCell>
                    <TableCell className="text-end ltr-number font-medium">
                      {parseFloat(line.credit) > 0 ? formatCurrency(line.credit, functionalCurrency) : "-"}
                    </TableCell>
                    <TableCell className="text-center">
                      {line.reconciled && (
                        <CheckCircle2 className="h-4 w-4 text-green-600 mx-auto" />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {/* Totals Row */}
                <TableRow className="font-bold border-t-2">
                  <TableCell colSpan={3} className="text-end">
                    {t("accounting:totals.totalDebit")} / {t("accounting:totals.totalCredit")}
                  </TableCell>
                  {isForeignCurrency && <TableCell />}
                  <TableCell className="text-end ltr-number">
                    {formatCurrency(entry.total_debit, functionalCurrency)}
                  </TableCell>
                  <TableCell className="text-end ltr-number">
                    {formatCurrency(entry.total_credit, functionalCurrency)}
                  </TableCell>
                  <TableCell />
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* Back link */}
        <div>
          <Link href="/accounting/journal-entries">
            <Button variant="ghost">
              <ArrowLeft className="me-2 h-4 w-4" />
              {t("actions.back")}
            </Button>
          </Link>
        </div>
      </div>

      {/* Confirm Dialogs */}
      <ConfirmDialog
        open={showPostConfirm}
        onOpenChange={setShowPostConfirm}
        title={t("accounting:journalEntries.postEntry")}
        description={t("accounting:messages.postConfirm")}
        onConfirm={handlePost}
        isLoading={postEntry.isPending}
      />
      {/* Profile NONE: simple confirmation (empty-body reverse) */}
      <ConfirmDialog
        open={showReverseConfirm}
        onOpenChange={setShowReverseConfirm}
        title={t("accounting:journalEntries.reverseEntry")}
        description={t("accounting:messages.reverseConfirm")}
        onConfirm={handleReverse}
        isLoading={reverseEntry.isPending}
      />

      {/* A5-PR4b: active-pilot reversal input dialog */}
      <Dialog
        open={showReverseDialog}
        onOpenChange={(open) => {
          setShowReverseDialog(open);
          if (!open) resetReverseFields();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("accounting:journalEntries.reverseEntry")}</DialogTitle>
            <DialogDescription>
              This posts a reversing entry. It does not resolve or repair the
              referenced source item.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="reverse-reason">Reversal reason</Label>
              <Textarea
                id="reverse-reason"
                value={reverseReason}
                onChange={(e) => setReverseReason(e.target.value)}
                placeholder="Why is this adjustment being reversed?"
              />
              <p className="text-xs text-muted-foreground">Required · 10–180 characters</p>
            </div>

            {reversalInheritsSource ? (
              <div className="space-y-1 rounded-md border bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">
                  Inherited source (read-only) — the reversal keeps the original
                  adjustment&apos;s provenance.
                </p>
                <code className="font-mono text-xs break-all">{entry.source_document}</code>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-xs text-muted-foreground">
                  This entry has no pilot-adjustment provenance, so the reversal becomes
                  a new supervised pilot adjustment. Provide its source.
                </p>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Source type</Label>
                    <Select
                      value={reverseSourceKind}
                      onValueChange={(v) => {
                        setReverseSourceKind(v as PilotAdjustmentSourceKind);
                        setReverseSourceRef("");
                      }}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select a source type" />
                      </SelectTrigger>
                      <SelectContent>
                        {PILOT_ADJUSTMENT_SOURCE_KINDS.map((k) => (
                          <SelectItem key={k} value={k}>
                            {pilotAdjustmentKindLabel(k)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="reverse-source-ref">Source reference</Label>
                    <Input
                      id="reverse-source-ref"
                      value={reverseSourceRef}
                      onChange={(e) => setReverseSourceRef(e.target.value)}
                      placeholder="Source reference"
                    />
                    {reverseSourceKind && (
                      <p className="text-xs text-muted-foreground">
                        {pilotAdjustmentReferenceHint(reverseSourceKind)}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}

            {reverseError && <p className="text-sm text-destructive">{reverseError}</p>}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setShowReverseDialog(false);
                resetReverseFields();
              }}
              disabled={reverseEntry.isPending}
            >
              {t("actions.cancel")}
            </Button>
            <Button onClick={handleReversePilot} disabled={reverseEntry.isPending}>
              {reverseEntry.isPending
                ? t("actions.loading")
                : t("accounting:journalEntries.reverseEntry")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={showDeleteConfirm}
        onOpenChange={setShowDeleteConfirm}
        title={t("accounting:journalEntries.deleteEntry")}
        description={t("messages.confirmDelete")}
        onConfirm={handleDelete}
        isLoading={deleteEntry.isPending}
        variant="destructive"
      />
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])),
    },
  };
};
