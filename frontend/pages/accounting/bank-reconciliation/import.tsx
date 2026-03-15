import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { ArrowLeft, Upload, Loader2 } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { useAccounts } from "@/queries/useAccounts";
import { bankReconciliationService } from "@/services/bank-reconciliation.service";

export default function ImportStatementPage() {
  const router = useRouter();
  const { toast } = useToast();
  const { data: accounts } = useAccounts();

  const [form, setForm] = useState({
    account_id: "",
    statement_date: "",
    period_start: "",
    period_end: "",
    opening_balance: "",
    closing_balance: "",
    currency: "USD",
  });

  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [parsedLines, setParsedLines] = useState<
    Array<{ line_date: string; description: string; amount: string; reference: string }>
  >([]);
  const [parsing, setParsing] = useState(false);
  const [importing, setImporting] = useState(false);

  // Bank/cash accounts only
  const bankAccounts =
    accounts?.filter(
      (a) => !a.is_header && a.status === "ACTIVE" && a.account_type === "ASSET",
    ) || [];

  const handleParseCSV = async () => {
    if (!csvFile) return;
    setParsing(true);
    try {
      const formData = new FormData();
      formData.append("file", csvFile);

      const { data } = await bankReconciliationService.parseCSV(formData);
      setParsedLines(
        data.lines.map((l: Record<string, string>) => ({
          line_date: l.line_date || "",
          description: l.description || "",
          amount: l.amount || "0",
          reference: l.reference || "",
        })),
      );
      toast({ title: `Parsed ${data.count} lines from CSV.` });
    } catch {
      toast({ title: "Failed to parse CSV.", variant: "destructive" });
    } finally {
      setParsing(false);
    }
  };

  const handleImport = async () => {
    if (!form.account_id || !form.statement_date) {
      toast({ title: "Please fill in all required fields.", variant: "destructive" });
      return;
    }
    if (parsedLines.length === 0) {
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
      });
      toast({ title: `Statement imported with ${data.lines_created} lines.` });
      router.push(`/accounting/bank-reconciliation/${data.id}`);
    } catch {
      toast({ title: "Failed to import statement.", variant: "destructive" });
    } finally {
      setImporting(false);
    }
  };

  const updateForm = (key: string, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

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
                  className="w-full border rounded-md px-3 py-2 text-sm"
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
                <Input
                  type="date"
                  value={form.statement_date}
                  onChange={(e) => updateForm("statement_date", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Currency</Label>
                <Input
                  value={form.currency}
                  onChange={(e) => updateForm("currency", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Period Start</Label>
                <Input
                  type="date"
                  value={form.period_start}
                  onChange={(e) => updateForm("period_start", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Period End</Label>
                <Input
                  type="date"
                  value={form.period_end}
                  onChange={(e) => updateForm("period_end", e.target.value)}
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
              Upload a CSV file with columns: Date, Description, Amount, Reference (optional).
              The first row should be column headers.
            </p>
            <div className="flex gap-3 items-end">
              <div className="flex-1">
                <Input
                  type="file"
                  accept=".csv"
                  onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
                />
              </div>
              <Button onClick={handleParseCSV} disabled={!csvFile || parsing}>
                {parsing ? (
                  <Loader2 className="me-2 h-4 w-4 animate-spin" />
                ) : (
                  <Upload className="me-2 h-4 w-4" />
                )}
                Parse
              </Button>
            </div>

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

        {/* Import Action */}
        {parsedLines.length > 0 && (
          <div className="flex justify-end">
            <Button onClick={handleImport} disabled={importing} size="lg">
              {importing ? (
                <Loader2 className="me-2 h-4 w-4 animate-spin" />
              ) : (
                <Upload className="me-2 h-4 w-4" />
              )}
              Import {parsedLines.length} Lines
            </Button>
          </div>
        )}
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale ?? "en", ["common"])),
  },
});
