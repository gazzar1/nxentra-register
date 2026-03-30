import { useState, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useQuery } from "@tanstack/react-query";
import { Printer, BarChart3 } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useBilingualText } from "@/components/common/BilingualText";
import { useDimensionAnalysis, useDimensionDrilldown } from "@/queries/useReports";
import { dimensionsService } from "@/services/accounts.service";
import { periodsService } from "@/services/periods.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import type { DimensionAnalysisFilters, DimensionDrilldownFilters } from "@/types/report";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

export default function DimensionAnalysisPage() {
  const { t } = useTranslation(["common", "reports"]);
  const getText = useBilingualText();
  const { company } = useAuth();
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();

  const currentYear = new Date().getFullYear();
  const [dimensionCode, setDimensionCode] = useState<string>("");
  const [fiscalYear, setFiscalYear] = useState<number>(currentYear);
  const [periodFrom, setPeriodFrom] = useState<number | null>(null);
  const [periodTo, setPeriodTo] = useState<number | null>(null);

  // Drilldown state
  const [drilldownValueCode, setDrilldownValueCode] = useState<string | null>(null);

  // Fetch CONTEXT dimensions
  const { data: dimensions, isLoading: dimsLoading } = useQuery({
    queryKey: ["dimensions"],
    queryFn: async () => {
      const { data } = await dimensionsService.list();
      return data;
    },
  });

  const contextDimensions = useMemo(
    () => (dimensions ?? []).filter((d) => d.dimension_kind === "CONTEXT"),
    [dimensions]
  );

  // Fetch periods for the fiscal year
  const { data: periodsData } = useQuery({
    queryKey: ["periods", fiscalYear],
    queryFn: async () => {
      const { data } = await periodsService.list(fiscalYear);
      return data;
    },
  });

  const periodOptions = useMemo(() => {
    if (!periodsData?.periods) return [];
    return periodsData.periods
      .filter((p) => p.fiscal_year === fiscalYear)
      .sort((a, b) => a.period - b.period);
  }, [periodsData, fiscalYear]);

  const fiscalYearOptions = useMemo(() => {
    const years = [];
    for (let i = currentYear + 1; i >= currentYear - 4; i--) {
      years.push(i);
    }
    return years;
  }, [currentYear]);

  // Build query filters
  const filters: DimensionAnalysisFilters | null = useMemo(() => {
    if (!dimensionCode) return null;
    const f: DimensionAnalysisFilters = { dimension_code: dimensionCode };
    if (periodFrom && periodTo) {
      f.fiscal_year = fiscalYear;
      f.period_from = periodFrom;
      f.period_to = periodTo;
    }
    return f;
  }, [dimensionCode, fiscalYear, periodFrom, periodTo]);

  const { data: report, isLoading, isError } = useDimensionAnalysis(filters);

  // Drilldown query
  const drilldownFilters: DimensionDrilldownFilters | null = useMemo(() => {
    if (!dimensionCode || !drilldownValueCode) return null;
    const f: DimensionDrilldownFilters = {
      dimension_code: dimensionCode,
      value_code: drilldownValueCode,
    };
    if (periodFrom && periodTo) {
      f.fiscal_year = fiscalYear;
      f.period_from = periodFrom;
      f.period_to = periodTo;
    }
    return f;
  }, [dimensionCode, drilldownValueCode, fiscalYear, periodFrom, periodTo]);

  const { data: drilldown, isLoading: drilldownLoading } = useDimensionDrilldown(drilldownFilters);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Dimension Analysis"
          subtitle="Revenue and expenses grouped by analysis dimension"
        />

        {/* Filters */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-end gap-4">
              {/* Dimension selector */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Dimension</label>
                <Select value={dimensionCode} onValueChange={setDimensionCode}>
                  <SelectTrigger className="w-[200px]">
                    <SelectValue placeholder="Select dimension" />
                  </SelectTrigger>
                  <SelectContent>
                    {contextDimensions.map((d) => (
                      <SelectItem key={d.code} value={d.code}>
                        {getText(d.name, d.name_ar)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Fiscal year */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Fiscal Year</label>
                <Select
                  value={String(fiscalYear)}
                  onValueChange={(v) => setFiscalYear(Number(v))}
                >
                  <SelectTrigger className="w-[120px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {fiscalYearOptions.map((y) => (
                      <SelectItem key={y} value={String(y)}>
                        {y}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Period from */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Period From</label>
                <Select
                  value={periodFrom ? String(periodFrom) : ""}
                  onValueChange={(v) => setPeriodFrom(v ? Number(v) : null)}
                >
                  <SelectTrigger className="w-[140px]">
                    <SelectValue placeholder="All" />
                  </SelectTrigger>
                  <SelectContent>
                    {periodOptions.map((p) => (
                      <SelectItem key={p.period} value={String(p.period)}>
                        P{p.period} ({p.start_date})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Period to */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Period To</label>
                <Select
                  value={periodTo ? String(periodTo) : ""}
                  onValueChange={(v) => setPeriodTo(v ? Number(v) : null)}
                >
                  <SelectTrigger className="w-[140px]">
                    <SelectValue placeholder="All" />
                  </SelectTrigger>
                  <SelectContent>
                    {periodOptions.map((p) => (
                      <SelectItem key={p.period} value={String(p.period)}>
                        P{p.period} ({p.end_date})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Print */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => window.print()}
                disabled={!report}
              >
                <Printer className="h-4 w-4 mr-1" />
                Print
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Results */}
        {!dimensionCode && (
          <EmptyState
            icon={<BarChart3 className="h-12 w-12" />}
            title="Select a Dimension"
            description="Choose a CONTEXT dimension above to see revenue and expenses grouped by its values."
          />
        )}

        {isLoading && dimensionCode && <LoadingSpinner />}

        {isError && dimensionCode && (
          <Card>
            <CardContent className="py-8 text-center text-destructive">
              Failed to load report. Please try again.
            </CardContent>
          </Card>
        )}

        {report && (
          <Card>
            <CardHeader>
              <CardTitle>
                {getText(report.dimension_name, report.dimension_name_ar)}
                {report.date_from && report.date_to && (
                  <span className="text-sm font-normal text-muted-foreground ml-2">
                    ({report.date_from} to {report.date_to})
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {report.rows.length === 0 ? (
                <div className="py-8 text-center text-muted-foreground">
                  No data found for this dimension and period.
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[120px]">Code</TableHead>
                      <TableHead>Name</TableHead>
                      <TableHead className="text-right">Revenue</TableHead>
                      <TableHead className="text-right">Expenses</TableHead>
                      <TableHead className="text-right">Net Income</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.rows.map((row) => {
                      const netNum = parseFloat(row.net_income);
                      return (
                        <TableRow
                          key={row.value_code}
                          className="cursor-pointer hover:bg-muted/50"
                          onClick={() => setDrilldownValueCode(row.value_code)}
                        >
                          <TableCell className="font-mono">
                            {row.value_code}
                          </TableCell>
                          <TableCell>
                            {getText(row.value_name, row.value_name_ar)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatAmount(row.revenue)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatAmount(row.expenses)}
                          </TableCell>
                          <TableCell
                            className={cn(
                              "text-right tabular-nums font-medium",
                              netNum > 0
                                ? "text-green-600 dark:text-green-400"
                                : netNum < 0
                                ? "text-red-600 dark:text-red-400"
                                : ""
                            )}
                          >
                            {formatAmount(row.net_income)}
                          </TableCell>
                        </TableRow>
                      );
                    })}

                    {/* Totals row */}
                    <TableRow className="border-t-2 font-bold">
                      <TableCell />
                      <TableCell>Total</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatAmount(report.totals.revenue)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatAmount(report.totals.expenses)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right tabular-nums",
                          parseFloat(report.totals.net_income) > 0
                            ? "text-green-600 dark:text-green-400"
                            : parseFloat(report.totals.net_income) < 0
                            ? "text-red-600 dark:text-red-400"
                            : ""
                        )}
                      >
                        {formatAmount(report.totals.net_income)}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              )}

              {report.currency && (
                <p className="text-xs text-muted-foreground mt-4">
                  All amounts in {report.currency}
                </p>
              )}
            </CardContent>
          </Card>
        )}

        {/* Drilldown Dialog */}
        <Dialog
          open={!!drilldownValueCode}
          onOpenChange={(open) => {
            if (!open) setDrilldownValueCode(null);
          }}
        >
          <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>
                {drilldown
                  ? `${getText(drilldown.dimension_name, drilldown.dimension_name_ar)}: ${getText(drilldown.value_name, drilldown.value_name_ar)} (${drilldown.value_code})`
                  : "Loading..."}
              </DialogTitle>
            </DialogHeader>

            {drilldownLoading ? (
              <div className="flex justify-center py-8">
                <LoadingSpinner />
              </div>
            ) : drilldown && drilldown.entries.length === 0 ? (
              <div className="py-8 text-center text-muted-foreground">
                No journal entries found for this dimension value.
              </div>
            ) : drilldown ? (
              <div className="space-y-4">
                {drilldown.date_from && drilldown.date_to && (
                  <p className="text-sm text-muted-foreground">
                    Period: {drilldown.date_from} to {drilldown.date_to}
                  </p>
                )}
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[100px]">Date</TableHead>
                      <TableHead className="w-[80px]">Account</TableHead>
                      <TableHead>Description</TableHead>
                      <TableHead className="text-right w-[120px]">Debit</TableHead>
                      <TableHead className="text-right w-[120px]">Credit</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {drilldown.entries.map((entry, idx) => (
                      <TableRow key={`${entry.entry_public_id}-${entry.line_no}`}>
                        <TableCell className="text-sm">{entry.entry_date}</TableCell>
                        <TableCell className="font-mono text-xs">
                          {entry.account_code}
                        </TableCell>
                        <TableCell className="text-sm">
                          <div>{getText(entry.account_name, entry.account_name_ar)}</div>
                          {entry.description && entry.description !== entry.account_name && (
                            <div className="text-xs text-muted-foreground">{entry.entry_memo}</div>
                          )}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {parseFloat(entry.debit) > 0 ? formatAmount(entry.debit) : "-"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {parseFloat(entry.credit) > 0 ? formatAmount(entry.credit) : "-"}
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="border-t-2 font-bold">
                      <TableCell colSpan={3}>Total</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatAmount(drilldown.total_debit)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatAmount(drilldown.total_credit)}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
                {drilldown.currency && (
                  <p className="text-xs text-muted-foreground">
                    All amounts in {drilldown.currency}
                  </p>
                )}
              </div>
            ) : null}
          </DialogContent>
        </Dialog>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "reports"])),
    },
  };
};
