import { useEffect, useRef, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { ArrowLeft, Upload, Loader2, Settings2 } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { CompanyDateInput, type DateFormat } from "@/components/ui/CompanyDateInput";
import { Label } from "@/components/ui/label";
import { PageHeader, CsvMappingDialog, type ColumnMapping } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { useAccounts } from "@/queries/useAccounts";
import { useAuth } from "@/contexts/AuthContext";
import { currencyOptions } from "@/lib/constants";
import {
  bankReconciliationService,
  type ImportRejectDescriptor,
} from "@/services/bank-reconciliation.service";

const MAPPING_STORAGE_KEY = "nxentra:bank-import-mapping";

function loadSavedMapping(accountId: string): Partial<ColumnMapping> | undefined {
  if (!accountId || typeof window === "undefined") return undefined;
  try {
    const raw = window.localStorage.getItem(`${MAPPING_STORAGE_KEY}:${accountId}`);
    return raw ? (JSON.parse(raw) as Partial<ColumnMapping>) : undefined;
  } catch {
    return undefined;
  }
}

function saveMapping(accountId: string, mapping: ColumnMapping) {
  if (!accountId || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      `${MAPPING_STORAGE_KEY}:${accountId}`,
      JSON.stringify(mapping),
    );
  } catch {
    // localStorage can be disabled — silently ignore.
  }
}

export default function ImportStatementPage() {
  const router = useRouter();
  const { toast } = useToast();
  const { data: accounts } = useAccounts();
  const { company } = useAuth();
  const companyCurrency = company?.default_currency || "USD";
  const companyDateFormat = (company?.date_format as DateFormat) || "YYYY-MM-DD";

  const [form, setForm] = useState({
    account_id: "",
    statement_date: "",
    period_start: "",
    period_end: "",
    opening_balance: "",
    closing_balance: "",
    currency: companyCurrency,
  });

  // Pick up the company default once auth context loads (initial state
  // can land before the profile fetch resolves).
  useEffect(() => {
    setForm((prev) =>
      prev.currency === "USD" && companyCurrency !== "USD"
        ? { ...prev, currency: companyCurrency }
        : prev,
    );
  }, [companyCurrency]);

  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [sampleRows, setSampleRows] = useState<Array<Record<string, string>>>([]);
  const [mapping, setMapping] = useState<ColumnMapping | null>(null);
  const [parsedLines, setParsedLines] = useState<
    Array<{ line_date: string; description: string; amount: string; reference: string }>
  >([]);
  // A5-PR3c: rows the parser dropped — echoed back on the commit so they
  // become durable ImportRejectedRow evidence (visible in /finance/exceptions).
  const [parseRejects, setParseRejects] = useState<ImportRejectDescriptor[]>([]);
  const [parseFilename, setParseFilename] = useState("");
  const [parsingHeaders, setParsingHeaders] = useState(false);
  const [parsingLines, setParsingLines] = useState(false);
  const [importing, setImporting] = useState(false);
  const [mapDialogOpen, setMapDialogOpen] = useState(false);
  // A5-PR3c (Codex round-6): monotonic parse generation. Selecting a new file
  // (or account) bumps it; an in-flight parse response captured under an older
  // generation is DISCARDED, so a slow response for file A can never repopulate
  // state (and re-enable the reject-only action) after file B was selected.
  const parseGenRef = useRef(0);

  // Bank/cash accounts only
  const bankAccounts =
    accounts?.filter(
      (a) => !a.is_header && a.status === "ACTIVE" && a.account_type === "ASSET",
    ) || [];

  // Reset parsed state when account changes — saved mapping is per-account.
  useEffect(() => {
    parseGenRef.current += 1;
    setHeaders([]);
    setSampleRows([]);
    setMapping(null);
    setParsedLines([]);
    setParseRejects([]);
    setParseFilename("");
  }, [form.account_id]);

  const handleParseHeaders = async () => {
    if (!csvFile) return;
    const gen = parseGenRef.current;
    setParsingHeaders(true);
    try {
      const formData = new FormData();
      formData.append("file", csvFile);
      const { data } = await bankReconciliationService.parseCSVHeaders(formData);
      if (gen !== parseGenRef.current) return; // stale file — discard
      setHeaders(data.headers);
      setSampleRows(data.sample_rows);
      setMapDialogOpen(true);
    } catch {
      toast({ title: "Failed to read CSV headers.", variant: "destructive" });
    } finally {
      setParsingHeaders(false);
    }
  };

  const handleConfirmMapping = async (m: ColumnMapping) => {
    setMapping(m);
    saveMapping(form.account_id, m);
    if (!csvFile) return;
    const gen = parseGenRef.current;
    setParsingLines(true);
    try {
      const formData = new FormData();
      formData.append("file", csvFile);
      formData.append("date_column", m.date_column);
      formData.append("description_column", m.description_column);
      formData.append("amount_column", m.amount_column);
      formData.append("reference_column", m.reference_column);
      formData.append("debit_column", m.debit_column);
      formData.append("credit_column", m.credit_column);
      formData.append("date_format", m.date_format);

      const { data } = await bankReconciliationService.parseCSV(formData);
      if (gen !== parseGenRef.current) return; // a different file was selected mid-flight — discard
      setParsedLines(
        data.lines.map((l: Record<string, string>) => ({
          line_date: l.line_date || "",
          description: l.description || "",
          amount: l.amount || "0",
          reference: l.reference || "",
        })),
      );
      setParseRejects(data.rejected_rows ?? []);
      setParseFilename(data.source_filename || csvFile.name || "");
      const rejectedCount = data.rejected_row_count ?? 0;
      if (data.count === 0 && rejectedCount > 0) {
        toast({
          title: `Parsed 0 lines — ${rejectedCount} row(s) rejected`,
          description:
            "If the column mapping is correct, you can still import to record the rejected rows as durable evidence.",
          variant: "destructive",
        });
      } else if (data.count === 0) {
        toast({
          title: "Parsed 0 lines",
          description:
            "Check the column mapping — the date column may not match the date format.",
          variant: "destructive",
        });
      } else if (rejectedCount > 0) {
        toast({
          title: `Parsed ${data.count} lines from CSV.`,
          description: `${rejectedCount} row(s) could not be parsed and will be recorded as import rejections on import.`,
        });
      } else {
        toast({ title: `Parsed ${data.count} lines from CSV.` });
      }
    } catch {
      toast({ title: "Failed to parse CSV.", variant: "destructive" });
    } finally {
      setParsingLines(false);
    }
  };

  const handleImport = async () => {
    const missing: string[] = [];
    if (!form.account_id) missing.push("Bank Account");
    if (!form.statement_date) missing.push("Statement Date");
    if (missing.length > 0) {
      toast({
        title: "Missing required fields",
        description: missing.join(", "),
        variant: "destructive",
      });
      return;
    }
    // A5-PR3c (Codex round-4): an all-invalid file (0 survivors, N rejects) is
    // still committable — the statement records with zero lines and the
    // rejected rows become durable evidence in Finance → Exceptions.
    if (parsedLines.length === 0 && parseRejects.length === 0) {
      toast({ title: "No lines to import.", variant: "destructive" });
      return;
    }

    setImporting(true);
    try {
      const { data } = await bankReconciliationService.createStatement({
        account_id: Number(form.account_id),
        statement_date: form.statement_date,
        period_start: form.period_start || form.statement_date,
        period_end: form.period_end || form.statement_date,
        opening_balance: form.opening_balance || "0",
        closing_balance: form.closing_balance || "0",
        currency: form.currency,
        source: "CSV",
        lines: parsedLines,
        // A5-PR3c: persist the parse-time drops as durable evidence.
        parse_rejects: parseRejects,
        source_filename: parseFilename,
      });
      const skipped = data.lines_skipped_duplicate ?? 0;
      const rejected = data.lines_rejected ?? 0;
      const parts = [`Imported ${data.lines_created} transactions`];
      if (skipped > 0) parts.push(`skipped ${skipped} duplicates`);
      if (rejected > 0) parts.push(`recorded ${rejected} rejected row(s)`);
      toast({
        title: `${parts.join(", ")}.`,
        description:
          rejected > 0 ? "Rejected rows are listed under Finance → Exceptions." : undefined,
      });
      router.push(`/accounting/bank-reconciliation/${data.id}`);
    } catch {
      toast({ title: "Failed to import statement.", variant: "destructive" });
    } finally {
      setImporting(false);
    }
  };

  const updateForm = (key: string, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const savedMapping = loadSavedMapping(form.account_id);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Import Bank Statement"
          subtitle="Upload a CSV file and configure the import"
          actions={
            <Button
              variant="outline"
              onClick={() => router.push("/accounting/bank-reconciliation")}
            >
              <ArrowLeft className="me-2 h-4 w-4" />
              Back
            </Button>
          }
        />

        {/* Statement Details */}
        <Card>
          <CardHeader>
            <CardTitle>Statement Details</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-1.5">
                <Label>Bank Account *</Label>
                <select
                  className="w-full border rounded-md px-3 py-2 text-sm bg-background text-foreground"
                  value={form.account_id}
                  onChange={(e) => updateForm("account_id", e.target.value)}
                >
                  <option value="">Select account...</option>
                  {bankAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.code} — {a.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label>Statement Date *</Label>
                <CompanyDateInput
                  value={form.statement_date}
                  onChange={(v) => updateForm("statement_date", v)}
                  dateFormat={companyDateFormat}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Currency</Label>
                <select
                  className="w-full border rounded-md px-3 py-2 text-sm bg-background text-foreground"
                  value={form.currency}
                  onChange={(e) => updateForm("currency", e.target.value)}
                >
                  {currencyOptions.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label>Period Start</Label>
                <CompanyDateInput
                  value={form.period_start}
                  onChange={(v) => updateForm("period_start", v)}
                  dateFormat={companyDateFormat}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Period End</Label>
                <CompanyDateInput
                  value={form.period_end}
                  onChange={(v) => updateForm("period_end", v)}
                  dateFormat={companyDateFormat}
                />
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Opening Balance</Label>
                <Input
                  type="number"
                  step="0.01"
                  value={form.opening_balance}
                  onChange={(e) => updateForm("opening_balance", e.target.value)}
                  placeholder="0.00"
                />
              </div>
              <div className="space-y-1.5">
                <Label>Closing Balance</Label>
                <Input
                  type="number"
                  step="0.01"
                  value={form.closing_balance}
                  onChange={(e) => updateForm("closing_balance", e.target.value)}
                  placeholder="0.00"
                />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* CSV Upload */}
        <Card>
          <CardHeader>
            <CardTitle>Upload CSV</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Upload your bank&apos;s CSV export. We&apos;ll detect the columns
              automatically; you can review and adjust the mapping before
              parsing. Mappings are remembered per bank account.
            </p>
            <div className="flex gap-3 items-end">
              <div className="flex-1">
                <Input
                  type="file"
                  accept=".csv"
                  onChange={(e) => {
                    // A5-PR3c (Codex round-6): invalidate any in-flight parse
                    // for the previous file — a slow response must not
                    // repopulate state after this reset.
                    parseGenRef.current += 1;
                    setCsvFile(e.target.files?.[0] || null);
                    setHeaders([]);
                    setSampleRows([]);
                    setParsedLines([]);
                    // A5-PR3c (Codex round-5): clear the previous file's reject
                    // state too — otherwise the reject-only action could persist
                    // file A's evidence while file B is selected.
                    setParseRejects([]);
                    setParseFilename("");
                  }}
                />
              </div>
              <Button onClick={handleParseHeaders} disabled={!csvFile || parsingHeaders}>
                {parsingHeaders ? (
                  <Loader2 className="me-2 h-4 w-4 animate-spin" />
                ) : (
                  <Upload className="me-2 h-4 w-4" />
                )}
                Map columns
              </Button>
              {mapping && headers.length > 0 && (
                <Button
                  variant="outline"
                  onClick={() => setMapDialogOpen(true)}
                  disabled={parsingLines}
                  title="Re-map columns"
                >
                  <Settings2 className="me-2 h-4 w-4" />
                  Re-map
                </Button>
              )}
            </div>

            {parsingLines && (
              <p className="text-sm text-muted-foreground flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                Parsing rows...
              </p>
            )}

            {parsedLines.length > 0 && (
              <div className="mt-4">
                <p className="text-sm font-medium mb-2">
                  Preview ({parsedLines.length} lines)
                </p>
                <div className="overflow-x-auto max-h-[300px] overflow-y-auto border rounded-md">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-muted">
                      <tr className="text-left">
                        <th className="px-3 py-2 font-medium">Date</th>
                        <th className="px-3 py-2 font-medium">Description</th>
                        <th className="px-3 py-2 font-medium">Reference</th>
                        <th className="px-3 py-2 font-medium text-right">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {parsedLines.slice(0, 50).map((l, i) => (
                        <tr key={i} className="border-t">
                          <td className="px-3 py-1.5">{l.line_date}</td>
                          <td className="px-3 py-1.5 max-w-[250px] truncate">
                            {l.description}
                          </td>
                          <td className="px-3 py-1.5 text-muted-foreground">
                            {l.reference}
                          </td>
                          <td
                            className={`px-3 py-1.5 text-right font-mono ${
                              Number(l.amount) >= 0 ? "text-green-700" : "text-red-700"
                            }`}
                          >
                            {Number(l.amount).toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Import Action — also shown for an all-invalid file (0 lines, N
            rejects) so the rejected rows can still be recorded as durable
            evidence (A5-PR3c, Codex round-4). */}
        {(parsedLines.length > 0 || parseRejects.length > 0) && (
          <div className="flex flex-col items-end gap-1">
            {parseRejects.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {parseRejects.length} rejected row(s) will be recorded under Finance → Exceptions.
              </p>
            )}
            <Button onClick={handleImport} disabled={importing} size="lg">
              {importing ? (
                <Loader2 className="me-2 h-4 w-4 animate-spin" />
              ) : (
                <Upload className="me-2 h-4 w-4" />
              )}
              {parsedLines.length > 0
                ? `Import ${parsedLines.length} Lines`
                : `Record ${parseRejects.length} Rejected Row(s)`}
            </Button>
          </div>
        )}
      </div>

      <CsvMappingDialog
        open={mapDialogOpen}
        onOpenChange={setMapDialogOpen}
        headers={headers}
        sampleRows={sampleRows}
        initialMapping={savedMapping}
        onConfirm={handleConfirmMapping}
      />
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale ?? "en", ["common"])),
  },
});
