import { useEffect, useState } from "react";
import type { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import {
  Upload,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Truck,
  Wallet,
  FileText,
  ExternalLink,
} from "lucide-react";

import { AppLayout } from "@/components/layout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  settlementImportsService,
  type SettlementImportBatch,
  type SettlementImportResponse,
  type SettlementProviderCode,
} from "@/services/settlement-imports.service";
// A85 chunk 5 (2026-05-26): pre-flight preview modal.
import { JEPreviewModal } from "@/components/finance/JEPreviewModal";
import {
  settlementImportService,
  type OpenFiscalPeriod,
  type SettlementImportPreview,
} from "@/services/settlement-import.service";
import { useAuth } from "@/contexts/AuthContext";

interface UploaderState {
  file: File | null;
  uploading: boolean;
  result: SettlementImportResponse | null;
  error: string | null;
}

const INITIAL_STATE: UploaderState = {
  file: null,
  uploading: false,
  result: null,
  error: null,
};

function formatMoney(s: string): string {
  const n = Number(s);
  if (!Number.isFinite(n)) return s;
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export default function SettlementImportPage() {
  const { toast } = useToast();
  const { hasPermission } = useAuth();

  const [paymob, setPaymob] = useState<UploaderState>(INITIAL_STATE);
  const [bosta, setBosta] = useState<UploaderState>(INITIAL_STATE);

  // A85 chunk 5: pre-flight modal state.
  const [previewModalOpen, setPreviewModalOpen] = useState(false);
  const [preview, setPreview] = useState<SettlementImportPreview | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [pendingProvider, setPendingProvider] = useState<SettlementProviderCode | null>(null);
  const [posting, setPosting] = useState(false);
  const [openPeriods, setOpenPeriods] = useState<OpenFiscalPeriod[]>([]);

  // Load OPEN fiscal periods once for the override picker dropdown.
  useEffect(() => {
    settlementImportService
      .listOpenPeriods()
      .then(setOpenPeriods)
      .catch(() => setOpenPeriods([]));
  }, []);

  const userPermissions = (() => {
    const s = new Set<string>();
    if (hasPermission("accounting.je.override_period")) {
      s.add("accounting.je.override_period");
    }
    return s;
  })();

  // Step 1: operator clicks Import → fetch preview (no commit yet).
  const handleUpload = async (
    provider: SettlementProviderCode,
    state: UploaderState,
    setState: (s: UploaderState) => void
  ) => {
    if (!state.file) return;
    setState({ ...state, uploading: true, error: null, result: null });
    try {
      const previewData = await settlementImportService.preview(state.file, provider);
      setState({ ...state, uploading: false, error: null, result: null });
      setPreview(previewData);
      setPendingFile(state.file);
      setPendingProvider(provider);
      setPreviewModalOpen(true);
    } catch (err: any) {
      const message = err?.response?.data?.detail || err?.message || "Preview failed.";
      setState({ ...state, uploading: false, error: message, result: null });
      toast({
        title: `${provider} preview failed.`,
        description: message,
        variant: "destructive",
      });
    }
  };

  // Step 2: operator clicks Post All in modal → commit (with optional override).
  const handleConfirmCommit = async (
    override?: { period: number; fiscalYear: number; reason: string }
  ) => {
    if (!pendingFile || !pendingProvider) return;
    setPosting(true);
    try {
      const result = await settlementImportService.commit({
        file: pendingFile,
        provider: pendingProvider,
        periodOverride: override?.period,
        fiscalYearOverride: override?.fiscalYear,
        overrideReason: override?.reason,
      });

      // Show the result in the per-provider uploader card.
      const setter = pendingProvider === "paymob" ? setPaymob : setBosta;
      setter({
        file: pendingFile,
        uploading: false,
        result: result as unknown as SettlementImportResponse,
        error: null,
      });

      const newBatches = result.batches.filter((b) => !b.deduplicated).length;
      const dupBatches = result.batches.length - newBatches;
      toast({
        title:
          newBatches > 0
            ? `Imported ${newBatches} batch(es) from ${pendingProvider}${
                override ? " (period overridden)" : ""
              }.`
            : `No new batches — all ${dupBatches} were already imported.`,
      });

      setPreviewModalOpen(false);
      setPreview(null);
      setPendingFile(null);
      setPendingProvider(null);
    } catch (err: any) {
      const message =
        err?.response?.data?.detail || err?.message || "Commit failed.";
      toast({
        title: `${pendingProvider} commit failed.`,
        description: message,
        variant: "destructive",
      });
    } finally {
      setPosting(false);
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Import Settlements"
          subtitle="Upload Paymob and Bosta payout statements to drain provider clearing balances"
        />

        <Card>
          <CardHeader>
            <CardTitle>How this works</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>
              When Paymob or Bosta pays out, download the statement they email or expose in their
              dashboard, then upload it here. Each row in the CSV becomes a per-order line on the
              import event; rows are aggregated by payout batch and one journal entry is posted per
              batch:
            </p>
            <pre className="ml-2 mt-2 rounded bg-muted p-3 font-mono text-xs">
              {`DR Expected Bank Deposit  net
DR Gateway/Courier Fees   fees
DR Sales Returns          uncollected (Bosta failed deliveries only)
    CR <Provider> Clearing   gross  [tagged settlement_provider]`}
            </pre>
            <p>
              When the bank deposit lands and the bank-rec page matches it against the Expected Bank
              Deposit balance, the chain closes. View progress on{" "}
              <Link
                href="/finance/reconciliation"
                className="inline-flex items-center gap-1 text-primary underline"
              >
                Reconciliation <ExternalLink className="h-3 w-3" />
              </Link>
              .
            </p>
            <p>
              Re-uploading the same statement is safe — the importer dedupes by{" "}
              <span className="font-mono">payout_batch_id</span>, so you&apos;ll see a clear count of
              how many batches were new vs already-imported.
            </p>
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <ProviderUploader
            provider="paymob"
            title="Paymob"
            icon={<Wallet className="h-5 w-5" />}
            hint="Expected columns: order_id, gross, fee, net, payout_batch_id, payout_date"
            state={paymob}
            setState={setPaymob}
            onUpload={() => handleUpload("paymob", paymob, setPaymob)}
          />
          <ProviderUploader
            provider="bosta"
            title="Bosta"
            icon={<Truck className="h-5 w-5" />}
            hint="Expected columns: shipment_id, order_id, collected, courier_fee, net, batch_id, payout_date, status"
            state={bosta}
            setState={setBosta}
            onUpload={() => handleUpload("bosta", bosta, setBosta)}
          />
        </div>

        {/* A85 chunk 5: pre-flight preview modal. */}
        <JEPreviewModal
          open={previewModalOpen}
          onOpenChange={(open) => {
            setPreviewModalOpen(open);
            if (!open) {
              setPreview(null);
              setPendingFile(null);
              setPendingProvider(null);
            }
          }}
          preview={preview}
          userPermissions={userPermissions}
          openPeriods={openPeriods}
          onConfirm={handleConfirmCommit}
          isPosting={posting}
        />
      </div>
    </AppLayout>
  );
}

// =============================================================================
// Sub-components
// =============================================================================

function ProviderUploader({
  provider,
  title,
  icon,
  hint,
  state,
  setState,
  onUpload,
}: {
  provider: SettlementProviderCode;
  title: string;
  icon: JSX.Element;
  hint: string;
  state: UploaderState;
  setState: (s: UploaderState) => void;
  onUpload: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {icon}
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">{hint}</p>
        <div className="space-y-2">
          <Label htmlFor={`${provider}-file`}>CSV file</Label>
          <Input
            id={`${provider}-file`}
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => {
              const file = e.target.files?.[0] ?? null;
              setState({ ...state, file, error: null, result: null });
            }}
            disabled={state.uploading}
          />
        </div>
        <Button onClick={onUpload} disabled={!state.file || state.uploading} className="w-full">
          {state.uploading ? (
            <Loader2 className="me-2 h-4 w-4 animate-spin" />
          ) : (
            <Upload className="me-2 h-4 w-4" />
          )}
          {state.uploading ? "Importing..." : `Import ${title} CSV`}
        </Button>

        {state.error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <span>{state.error}</span>
          </div>
        )}

        {state.result && state.result.batches.length === 0 && (
          <div className="rounded-md border bg-muted/40 p-3 text-xs text-muted-foreground italic">
            No batches found in this CSV.
          </div>
        )}

        {state.result && state.result.batches.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium">Imported batches</p>
            <div className="space-y-2">
              {state.result.batches.map((b) => (
                <BatchResult key={b.batch_id} batch={b} />
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function BatchResult({ batch }: { batch: SettlementImportBatch }) {
  const orphans = batch.unknown_order_ids ?? [];
  const hasOrphans = orphans.length > 0;
  return (
    <div className="rounded-md border p-3 text-xs">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <FileText className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-mono font-medium">{batch.batch_id}</span>
        {batch.deduplicated ? (
          <Badge variant="outline">Already imported</Badge>
        ) : (
          <Badge variant="success">
            <CheckCircle2 className="me-1 h-3 w-3" />
            Imported
          </Badge>
        )}
        {hasOrphans && (
          // A26: this batch referenced orders the system has never seen.
          // JE still posted, but provider clearing may go negative on the
          // orphaned portion — the merchant should investigate.
          <Badge variant="destructive" title="Some referenced orders were not found in Shopify history.">
            <AlertCircle className="me-1 h-3 w-3" />
            Needs review
          </Badge>
        )}
      </div>
      <div className="grid grid-cols-4 gap-2 text-muted-foreground">
        <div>
          <span className="block uppercase">Gross</span>
          <span className="font-mono text-foreground">{formatMoney(batch.gross)}</span>
        </div>
        <div>
          <span className="block uppercase">Fees</span>
          <span className="font-mono text-foreground">{formatMoney(batch.fees)}</span>
        </div>
        <div>
          <span className="block uppercase">Net</span>
          <span className="font-mono text-foreground">{formatMoney(batch.net)}</span>
        </div>
        <div>
          <span className="block uppercase">Uncollected</span>
          <span className="font-mono text-foreground">{formatMoney(batch.uncollected)}</span>
        </div>
      </div>
      <div className="mt-2 text-[11px] text-muted-foreground">{batch.line_count} line item(s)</div>
      {hasOrphans && (
        <div className="mt-2 rounded border border-destructive/30 bg-destructive/5 p-2 text-[11px]">
          <p className="mb-1 font-medium text-destructive">
            {orphans.length} order ID{orphans.length === 1 ? "" : "s"} not found in Shopify history:
          </p>
          <p className="font-mono text-muted-foreground break-all">
            {orphans.slice(0, 10).join(", ")}
            {orphans.length > 10 && <span> … (+{orphans.length - 10} more)</span>}
          </p>
          <p className="mt-1 text-muted-foreground">
            The JE still posted, but the orphaned portion will short-pay provider clearing until you import the missing orders.
          </p>
        </div>
      )}
    </div>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
