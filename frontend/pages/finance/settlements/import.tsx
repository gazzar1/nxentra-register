import { useState } from "react";
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

  const [paymob, setPaymob] = useState<UploaderState>(INITIAL_STATE);
  const [bosta, setBosta] = useState<UploaderState>(INITIAL_STATE);

  const handleUpload = async (
    provider: SettlementProviderCode,
    state: UploaderState,
    setState: (s: UploaderState) => void
  ) => {
    if (!state.file) return;
    setState({ ...state, uploading: true, error: null, result: null });
    try {
      const { data } = await settlementImportsService.importCsv(state.file, provider);
      setState({
        file: state.file,
        uploading: false,
        result: data,
        error: null,
      });
      const newBatches = data.batches.filter((b) => !b.deduplicated).length;
      const dupBatches = data.batches.length - newBatches;
      toast({
        title:
          newBatches > 0
            ? `Imported ${newBatches} batch(es) from ${provider}.`
            : `No new batches — all ${dupBatches} were already imported.`,
      });
    } catch (err: any) {
      const message = err?.response?.data?.detail || err?.message || "Import failed.";
      setState({ ...state, uploading: false, error: message, result: null });
      toast({ title: `${provider} import failed.`, description: message, variant: "destructive" });
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
  return (
    <div className="rounded-md border p-3 text-xs">
      <div className="mb-2 flex items-center gap-2">
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
