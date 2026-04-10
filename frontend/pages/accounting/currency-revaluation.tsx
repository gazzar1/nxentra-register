import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, ArrowUpDown, TrendingUp, TrendingDown, Send, AlertTriangle, RotateCcw } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { CompanyDateInput } from "@/components/ui/CompanyDateInput";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, LoadingSpinner, ConfirmDialog } from "@/components/common";
import { ContextualHelp } from "@/components/common/ContextualHelp";
import { useAuth } from "@/contexts/AuthContext";
import { useToast } from "@/components/ui/toaster";
import { reportsService } from "@/services/reports.service";
import { getErrorMessage } from "@/lib/api-client";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

export default function CurrencyRevaluationPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { company } = useAuth();
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();
  const { toast } = useToast();
  const functionalCurrency = company?.functional_currency || company?.default_currency || "USD";

  const [revaluationDate, setRevaluationDate] = useState(
    new Date().toISOString().split("T")[0]
  );
  const [showPostConfirm, setShowPostConfirm] = useState(false);
  const [isPosting, setIsPosting] = useState(false);
  const [autoReverse, setAutoReverse] = useState(true);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["currency-revaluation", revaluationDate],
    queryFn: () =>
      reportsService
        .currencyRevaluation({ revaluation_date: revaluationDate })
        .then((r) => r.data),
    enabled: !!revaluationDate,
  });

  // formatCurrency provided by useCompanyFormat hook

  const handlePost = async () => {
    setIsPosting(true);
    try {
      const { data: result } = await reportsService.postCurrencyRevaluation({
        revaluation_date: revaluationDate,
        auto_reverse: autoReverse,
      });
      if (result.post_error) {
        toast({
          title: "Entry created but not posted",
          description: result.post_error,
          variant: "destructive",
        });
      } else {
        const desc = result.reversal_date
          ? `${result.message} Auto-reversal scheduled for ${result.reversal_date}.`
          : result.message;
        toast({
          title: t("messages.success"),
          description: desc,
          variant: "success",
        });
      }
      setShowPostConfirm(false);
      if (result.entry_id) {
        router.push(`/accounting/journal-entries/${result.entry_id}`);
      }
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setIsPosting(false);
    }
  };

  const totalGainLoss = data ? parseFloat(data.total_gain_loss) : 0;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Currency Revaluation"
          subtitle={`Revalue foreign currency balances to ${functionalCurrency}`}
          actions={
            data?.has_adjustments ? (
              <Button onClick={() => setShowPostConfirm(true)}>
                <Send className="me-2 h-4 w-4" />
                Post Revaluation Entry
              </Button>
            ) : undefined
          }
        />

        {/* Date Selector */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-end gap-4">
              <div className="space-y-2">
                <Label htmlFor="reval-date">Revaluation Date</Label>
                <CompanyDateInput
                  id="reval-date"
                  value={revaluationDate}
                  onChange={(iso) => setRevaluationDate(iso)}
                  dateFormat={(company?.date_format as any) || "YYYY-MM-DD"}
                  className="w-48"
                />
              </div>
              <Button variant="outline" onClick={() => refetch()}>
                <RefreshCw className="me-2 h-4 w-4" />
                Recalculate
              </Button>
              <div className="flex items-center gap-2 ms-4">
                <input
                  type="checkbox"
                  id="auto-reverse"
                  checked={autoReverse}
                  onChange={(e) => setAutoReverse(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300"
                />
                <Label htmlFor="auto-reverse" className="flex items-center gap-1 text-sm cursor-pointer">
                  <RotateCcw className="h-3.5 w-3.5 text-muted-foreground" />
                  Auto-reverse on 1st of next period
                </Label>
              </div>
            </div>
          </CardContent>
        </Card>

        <ContextualHelp items={[
          { question: "When should I run currency revaluation?", answer: "Run it at the end of each month or period before closing. It recalculates foreign currency balances at current exchange rates and records any unrealized gains or losses." },
          { question: "What does auto-reverse mean?", answer: "When enabled, the system creates a second entry on the first day of the next period that reverses the revaluation. This is standard accounting practice — the unrealized gain/loss is temporary until the actual transaction settles." },
          { question: "What if I see 'skipped' currencies?", answer: "Currencies are skipped when no exchange rate is configured for the revaluation date. Go to Settings > Exchange Rates and add the missing rate, then re-run." },
          { question: "Can I run this automatically?", answer: "Yes. Configure the 'accounting.run_currency_revaluation' periodic task in Django admin (Celery Beat) to run monthly with auto-reverse enabled." },
        ]} />

        {/* Automation Note */}
        <Card className="border-blue-200 bg-blue-50/50 dark:border-blue-900 dark:bg-blue-950/20">
          <CardContent className="pt-4 pb-4">
            <div className="flex items-start gap-3">
              <RefreshCw className="h-5 w-5 text-blue-600 mt-0.5 shrink-0" />
              <div className="text-sm">
                <p className="font-medium text-blue-900 dark:text-blue-200">Automated Revaluation Available</p>
                <p className="text-blue-700 dark:text-blue-400 mt-1">
                  Currency revaluation can run automatically at period-end via Celery Beat.
                  Configure the <code className="text-xs bg-blue-100 dark:bg-blue-900 px-1 py-0.5 rounded">accounting.run_currency_revaluation</code> periodic task in Django admin to schedule monthly runs with auto-reverse.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Summary */}
        {data && (
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">
                  Accounts with FX Exposure
                </CardTitle>
                <ArrowUpDown className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {data.adjustments.length}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">
                  Net Unrealized {totalGainLoss >= 0 ? "Gain" : "Loss"}
                </CardTitle>
                {totalGainLoss >= 0 ? (
                  <TrendingUp className="h-4 w-4 text-green-500" />
                ) : (
                  <TrendingDown className="h-4 w-4 text-red-500" />
                )}
              </CardHeader>
              <CardContent>
                <div
                  className={`text-2xl font-bold ltr-number ${
                    totalGainLoss >= 0 ? "text-green-500" : "text-red-500"
                  }`}
                >
                  {formatCurrency(data.total_gain_loss)}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">
                  Functional Currency
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {data.functional_currency}
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Skipped Currencies Warning */}
        {(data?.skipped?.length ?? 0) > 0 && (
          <Card className="border-yellow-500/50">
            <CardContent className="pt-4">
              <div className="flex items-start gap-2 text-sm">
                <AlertTriangle className="h-4 w-4 text-yellow-500 mt-0.5 shrink-0" />
                <div>
                  <p className="font-medium text-yellow-500">
                    Some accounts were skipped (missing exchange rates)
                  </p>
                  <ul className="mt-1 text-muted-foreground space-y-0.5">
                    {data!.skipped!.map((s: { account_code: string; currency: string; reason: string }, i: number) => (
                      <li key={i}>
                        {s.account_code} ({s.currency}): {s.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Adjustments Table */}
        <Card>
          <CardHeader>
            <CardTitle>FX Revaluation Details</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : !data || data.adjustments.length === 0 ? (
              <p className="text-center text-muted-foreground py-8">
                No foreign currency adjustments needed for this date.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Account</TableHead>
                    <TableHead className="text-center">Currency</TableHead>
                    <TableHead className="text-end">Foreign Balance</TableHead>
                    <TableHead className="text-end">Rate</TableHead>
                    <TableHead className="text-end">
                      Book Value ({functionalCurrency})
                    </TableHead>
                    <TableHead className="text-end">
                      Revalued ({functionalCurrency})
                    </TableHead>
                    <TableHead className="text-end">Gain / Loss</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.adjustments.map((adj, idx) => {
                    const gainLoss = parseFloat(adj.unrealized_gain_loss);
                    return (
                      <TableRow key={idx}>
                        <TableCell>
                          <span className="font-mono text-xs me-2">
                            {adj.account_code}
                          </span>
                          <span className="text-muted-foreground">
                            {adj.account_name}
                          </span>
                        </TableCell>
                        <TableCell className="text-center">
                          <span className="inline-flex items-center rounded-full bg-blue-500/10 px-2 py-0.5 text-xs font-medium text-blue-500">
                            {adj.currency}
                          </span>
                        </TableCell>
                        <TableCell className="text-end ltr-number font-mono">
                          {formatCurrency(adj.foreign_balance, adj.currency)}
                        </TableCell>
                        <TableCell className="text-end ltr-number font-mono text-muted-foreground">
                          {parseFloat(adj.current_rate).toFixed(6)}
                        </TableCell>
                        <TableCell className="text-end ltr-number font-mono">
                          {formatCurrency(adj.current_functional_balance)}
                        </TableCell>
                        <TableCell className="text-end ltr-number font-mono">
                          {formatCurrency(adj.revalued_balance)}
                        </TableCell>
                        <TableCell
                          className={`text-end ltr-number font-mono font-medium ${
                            gainLoss >= 0 ? "text-green-500" : "text-red-500"
                          }`}
                        >
                          {gainLoss >= 0 ? "+" : ""}
                          {formatCurrency(adj.unrealized_gain_loss)}
                        </TableCell>
                      </TableRow>
                    );
                  })}

                  {/* Totals */}
                  <TableRow className="font-bold border-t-2">
                    <TableCell colSpan={6} className="text-end">
                      Net Unrealized Gain / Loss
                    </TableCell>
                    <TableCell
                      className={`text-end ltr-number font-mono ${
                        totalGainLoss >= 0 ? "text-green-500" : "text-red-500"
                      }`}
                    >
                      {totalGainLoss >= 0 ? "+" : ""}
                      {formatCurrency(data.total_gain_loss)}
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <ConfirmDialog
        open={showPostConfirm}
        onOpenChange={setShowPostConfirm}
        title="Post Currency Revaluation"
        description={`This will create and post an adjustment journal entry for ${formatCurrency(data?.total_gain_loss || "0")} in unrealized FX ${totalGainLoss >= 0 ? "gains" : "losses"}. Continue?`}
        onConfirm={handlePost}
        isLoading={isPosting}
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
