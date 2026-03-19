import { useState, useEffect, useRef } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import {
  Upload,
  FileSpreadsheet,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ArrowRight,
  X,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import {
  bankService,
  BankAccount,
  CsvPreviewResponse,
  ColumnMapping,
  ImportResult,
} from "@/services/bank.service";

const REQUIRED_FIELDS = [
  { key: "date", label: "Date", required: true },
  { key: "description", label: "Description", required: true },
] as const;

const AMOUNT_FIELDS = [
  { key: "amount", label: "Amount (single column)", group: "single" },
  { key: "credit", label: "Credit (money in)", group: "split" },
  { key: "debit", label: "Debit (money out)", group: "split" },
] as const;

const OPTIONAL_FIELDS = [
  { key: "reference", label: "Reference / Check #" },
  { key: "balance", label: "Running Balance" },
  { key: "value_date", label: "Value Date" },
] as const;

export default function BankImportPage() {
  const router = useRouter();
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);

  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  // Step state: select-account → upload → map-columns → importing → result
  const [step, setStep] = useState<
    "select-account" | "upload" | "map-columns" | "importing" | "result"
  >("upload");

  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvPreviewResponse | null>(null);
  const [uploading, setUploading] = useState(false);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [amountMode, setAmountMode] = useState<"single" | "split">("single");
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const { data } = await bankService.getAccounts();
        setAccounts(data);
        // Pre-select from query param
        const qId = Number(router.query.account);
        if (qId && data.find((a) => a.id === qId)) {
          setSelectedAccountId(qId);
          setStep("upload");
        } else if (data.length === 1) {
          setSelectedAccountId(data[0].id);
          setStep("upload");
        } else if (data.length === 0) {
          setStep("select-account");
        }
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setUploading(true);
    try {
      const { data } = await bankService.previewCsv(f);
      setPreview(data);
      // Auto-detect columns
      autoDetectMapping(data.headers);
      setStep("map-columns");
    } catch {
      toast({ title: "Failed to read CSV file.", variant: "destructive" });
    } finally {
      setUploading(false);
    }
  }

  function autoDetectMapping(headers: string[]) {
    const m: Record<string, string> = {};
    const lower = headers.map((h) => h.toLowerCase());

    // Date
    const dateIdx = lower.findIndex(
      (h) => h.includes("date") && !h.includes("value")
    );
    if (dateIdx >= 0) m.date = headers[dateIdx];

    // Value date
    const vdIdx = lower.findIndex((h) => h.includes("value") && h.includes("date"));
    if (vdIdx >= 0) m.value_date = headers[vdIdx];

    // Description
    const descIdx = lower.findIndex(
      (h) =>
        h.includes("description") ||
        h.includes("narrative") ||
        h.includes("details") ||
        h.includes("memo") ||
        h.includes("particulars")
    );
    if (descIdx >= 0) m.description = headers[descIdx];

    // Reference
    const refIdx = lower.findIndex(
      (h) => h.includes("reference") || h.includes("check") || h.includes("cheque")
    );
    if (refIdx >= 0) m.reference = headers[refIdx];

    // Amount
    const amtIdx = lower.findIndex(
      (h) => h === "amount" || h === "transaction amount"
    );
    if (amtIdx >= 0) {
      m.amount = headers[amtIdx];
      setAmountMode("single");
    } else {
      // Check for credit/debit split
      const creditIdx = lower.findIndex(
        (h) => h.includes("credit") || h.includes("deposit")
      );
      const debitIdx = lower.findIndex(
        (h) => h.includes("debit") || h.includes("withdrawal")
      );
      if (creditIdx >= 0 || debitIdx >= 0) {
        setAmountMode("split");
        if (creditIdx >= 0) m.credit = headers[creditIdx];
        if (debitIdx >= 0) m.debit = headers[debitIdx];
      }
    }

    // Balance
    const balIdx = lower.findIndex(
      (h) => h.includes("balance") || h.includes("running")
    );
    if (balIdx >= 0) m.balance = headers[balIdx];

    setMapping(m);
  }

  async function handleImport() {
    if (!file || !selectedAccountId) return;

    // Validate mapping
    if (!mapping.date || !mapping.description) {
      toast({
        title: "Please map the Date and Description columns.",
        variant: "destructive",
      });
      return;
    }
    if (amountMode === "single" && !mapping.amount) {
      toast({ title: "Please map the Amount column.", variant: "destructive" });
      return;
    }
    if (amountMode === "split" && !mapping.credit && !mapping.debit) {
      toast({
        title: "Please map at least one of Credit or Debit columns.",
        variant: "destructive",
      });
      return;
    }

    setImporting(true);
    setStep("importing");
    try {
      const columnMapping: ColumnMapping = {
        date: mapping.date,
        description: mapping.description,
      };
      if (amountMode === "single") {
        columnMapping.amount = mapping.amount;
      } else {
        if (mapping.credit) columnMapping.credit = mapping.credit;
        if (mapping.debit) columnMapping.debit = mapping.debit;
      }
      if (mapping.reference) columnMapping.reference = mapping.reference;
      if (mapping.balance) columnMapping.balance = mapping.balance;
      if (mapping.value_date) columnMapping.value_date = mapping.value_date;

      const { data } = await bankService.importCsv(file, selectedAccountId, columnMapping);
      setResult(data);
      setStep("result");
      toast({ title: `Imported ${data.created} transactions.` });
    } catch {
      toast({ title: "Import failed.", variant: "destructive" });
      setStep("map-columns");
    } finally {
      setImporting(false);
    }
  }

  function resetImport() {
    setFile(null);
    setPreview(null);
    setMapping({});
    setResult(null);
    setStep("upload");
    if (fileRef.current) fileRef.current.value = "";
  }

  const selectedAccount = accounts.find((a) => a.id === selectedAccountId);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Import Bank Statement"
          subtitle="Upload a CSV file from your bank to import transactions"
        />

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : accounts.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-12 text-center">
              <AlertCircle className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">No Bank Accounts</h3>
              <p className="text-sm text-muted-foreground mb-4">
                You need to create a bank account before importing statements.
              </p>
              <Button onClick={() => router.push("/banking/accounts")}>
                Create Bank Account
              </Button>
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Account Selector */}
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center gap-4">
                  <div className="flex-1 space-y-1.5">
                    <Label>Bank Account</Label>
                    <select
                      className="w-full border rounded-md px-3 py-2 text-sm"
                      value={selectedAccountId ?? ""}
                      onChange={(e) => {
                        setSelectedAccountId(
                          e.target.value ? Number(e.target.value) : null
                        );
                        resetImport();
                      }}
                    >
                      <option value="">— Select bank account —</option>
                      {accounts.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.account_name} ({a.bank_name} · {a.currency})
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </CardContent>
            </Card>

            {selectedAccountId && step === "upload" && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Upload className="h-5 w-5" />
                    Upload CSV File
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div
                    className="border-2 border-dashed rounded-lg p-8 text-center cursor-pointer hover:border-primary/50 transition-colors"
                    onClick={() => fileRef.current?.click()}
                  >
                    {uploading ? (
                      <Loader2 className="h-8 w-8 animate-spin text-muted-foreground mx-auto mb-3" />
                    ) : (
                      <FileSpreadsheet className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
                    )}
                    <p className="text-sm font-medium mb-1">
                      {uploading
                        ? "Reading file..."
                        : "Click to select a CSV file"}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Supported formats: CSV (.csv)
                    </p>
                  </div>
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".csv"
                    className="hidden"
                    onChange={handleFileSelect}
                  />
                </CardContent>
              </Card>
            )}

            {/* Column Mapping */}
            {step === "map-columns" && preview && (
              <>
                <Card>
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <CardTitle>Map Columns</CardTitle>
                      <Badge variant="secondary">
                        {preview.total_rows} rows in {preview.filename}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-6">
                    <p className="text-sm text-muted-foreground">
                      Match each field to the corresponding column in your CSV.
                      We&apos;ve auto-detected what we could.
                    </p>

                    {/* Required fields */}
                    <div className="space-y-3">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                        Required Fields
                      </p>
                      <div className="grid gap-3 sm:grid-cols-2">
                        {REQUIRED_FIELDS.map((f) => (
                          <div key={f.key} className="space-y-1">
                            <Label>{f.label} *</Label>
                            <select
                              className="w-full border rounded-md px-3 py-2 text-sm"
                              value={mapping[f.key] ?? ""}
                              onChange={(e) =>
                                setMapping({ ...mapping, [f.key]: e.target.value })
                              }
                            >
                              <option value="">— Select column —</option>
                              {preview.headers.map((h) => (
                                <option key={h} value={h}>
                                  {h}
                                </option>
                              ))}
                            </select>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Amount mode */}
                    <div className="space-y-3">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                        Amount Fields
                      </p>
                      <div className="flex gap-3 mb-3">
                        <Button
                          variant={amountMode === "single" ? "default" : "outline"}
                          size="sm"
                          onClick={() => setAmountMode("single")}
                        >
                          Single Amount Column
                        </Button>
                        <Button
                          variant={amountMode === "split" ? "default" : "outline"}
                          size="sm"
                          onClick={() => setAmountMode("split")}
                        >
                          Separate Credit / Debit
                        </Button>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2">
                        {AMOUNT_FIELDS.filter((f) => f.group === amountMode).map(
                          (f) => (
                            <div key={f.key} className="space-y-1">
                              <Label>{f.label} *</Label>
                              <select
                                className="w-full border rounded-md px-3 py-2 text-sm"
                                value={mapping[f.key] ?? ""}
                                onChange={(e) =>
                                  setMapping({ ...mapping, [f.key]: e.target.value })
                                }
                              >
                                <option value="">— Select column —</option>
                                {preview.headers.map((h) => (
                                  <option key={h} value={h}>
                                    {h}
                                  </option>
                                ))}
                              </select>
                            </div>
                          )
                        )}
                      </div>
                    </div>

                    {/* Optional fields */}
                    <div className="space-y-3">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                        Optional Fields
                      </p>
                      <div className="grid gap-3 sm:grid-cols-3">
                        {OPTIONAL_FIELDS.map((f) => (
                          <div key={f.key} className="space-y-1">
                            <Label>{f.label}</Label>
                            <select
                              className="w-full border rounded-md px-3 py-2 text-sm"
                              value={mapping[f.key] ?? ""}
                              onChange={(e) =>
                                setMapping({ ...mapping, [f.key]: e.target.value })
                              }
                            >
                              <option value="">— Not mapped —</option>
                              {preview.headers.map((h) => (
                                <option key={h} value={h}>
                                  {h}
                                </option>
                              ))}
                            </select>
                          </div>
                        ))}
                      </div>
                    </div>
                  </CardContent>
                </Card>

                {/* Preview Table */}
                <Card>
                  <CardHeader>
                    <CardTitle>Data Preview (first {preview.preview_rows.length} rows)</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b">
                            {preview.headers.map((h) => (
                              <th
                                key={h}
                                className="text-left px-3 py-2 font-medium text-muted-foreground whitespace-nowrap"
                              >
                                {h}
                                {Object.values(mapping).includes(h) && (
                                  <Badge variant="secondary" className="ms-1 text-[10px]">
                                    {Object.entries(mapping).find(
                                      ([, v]) => v === h
                                    )?.[0]}
                                  </Badge>
                                )}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {preview.preview_rows.map((row, i) => (
                            <tr key={i} className="border-b">
                              {preview.headers.map((h) => (
                                <td
                                  key={h}
                                  className="px-3 py-2 whitespace-nowrap"
                                >
                                  {row[h] || ""}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>

                {/* Import Button */}
                <div className="flex gap-3 justify-end">
                  <Button variant="outline" onClick={resetImport}>
                    Cancel
                  </Button>
                  <Button onClick={handleImport} disabled={importing}>
                    {importing && (
                      <Loader2 className="me-2 h-4 w-4 animate-spin" />
                    )}
                    Import {preview.total_rows} Transactions
                    <ArrowRight className="ms-2 h-4 w-4" />
                  </Button>
                </div>
              </>
            )}

            {/* Importing */}
            {step === "importing" && (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-12">
                  <Loader2 className="h-8 w-8 animate-spin text-primary mb-4" />
                  <p className="font-medium">Importing transactions...</p>
                  <p className="text-sm text-muted-foreground mt-1">
                    This may take a moment for large files.
                  </p>
                </CardContent>
              </Card>
            )}

            {/* Result */}
            {step === "result" && result && (
              <Card>
                <CardContent className="pt-6">
                  <div className="flex flex-col items-center text-center">
                    <CheckCircle2 className="h-12 w-12 text-green-500 mb-4" />
                    <h3 className="text-lg font-semibold mb-2">Import Complete</h3>
                    <div className="grid gap-4 sm:grid-cols-4 w-full max-w-2xl mt-4">
                      <div className="rounded-lg border p-4">
                        <p className="text-2xl font-bold">{result.created}</p>
                        <p className="text-xs text-muted-foreground">
                          Transactions Created
                        </p>
                      </div>
                      <div className="rounded-lg border p-4">
                        <p className="text-2xl font-bold">{result.total_rows}</p>
                        <p className="text-xs text-muted-foreground">Total Rows</p>
                      </div>
                      <div className="rounded-lg border p-4">
                        <p className="text-2xl font-bold text-green-600">
                          {result.total_credits}
                        </p>
                        <p className="text-xs text-muted-foreground">Total Credits</p>
                      </div>
                      <div className="rounded-lg border p-4">
                        <p className="text-2xl font-bold text-red-600">
                          {result.total_debits}
                        </p>
                        <p className="text-xs text-muted-foreground">Total Debits</p>
                      </div>
                    </div>
                    {result.errors.length > 0 && (
                      <div className="mt-4 w-full max-w-2xl rounded-lg border border-yellow-200 bg-yellow-50 p-4 text-left">
                        <p className="text-sm font-medium text-yellow-800 mb-2">
                          {result.errors.length} rows had issues:
                        </p>
                        {result.errors.map((err, i) => (
                          <p key={i} className="text-xs text-yellow-700">
                            {err}
                          </p>
                        ))}
                      </div>
                    )}
                    <div className="flex gap-3 mt-6">
                      <Button variant="outline" onClick={resetImport}>
                        Import Another
                      </Button>
                      <Button
                        onClick={() =>
                          router.push(
                            `/banking/transactions?account=${selectedAccountId}`
                          )
                        }
                      >
                        View Transactions
                        <ArrowRight className="ms-2 h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}
          </>
        )}
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
