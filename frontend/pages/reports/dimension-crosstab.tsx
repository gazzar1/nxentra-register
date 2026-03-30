import { useState, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useQuery } from "@tanstack/react-query";
import { Printer } from "lucide-react";
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
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useBilingualText } from "@/components/common/BilingualText";
import { useDimensionCrossTab } from "@/queries/useReports";
import { dimensionsService } from "@/services/accounts.service";
import { periodsService } from "@/services/periods.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import type { DimensionCrossTabFilters } from "@/types/report";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

export default function DimensionCrossTabPage() {
  const { t } = useTranslation(["common", "reports"]);
  const getText = useBilingualText();
  const { company } = useAuth();
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();

  const currentYear = new Date().getFullYear();
  const [rowDimension, setRowDimension] = useState<string>("");
  const [colDimension, setColDimension] = useState<string>("");
  const [metric, setMetric] = useState<string>("net_income");
  const [fiscalYear, setFiscalYear] = useState<number>(currentYear);
  const [periodFrom, setPeriodFrom] = useState<number | null>(null);
  const [periodTo, setPeriodTo] = useState<number | null>(null);

  // Fetch CONTEXT dimensions
  const { data: dimensions } = useQuery({
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

  // Fetch periods
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
  const filters: DimensionCrossTabFilters | null = useMemo(() => {
    if (!rowDimension || !colDimension || rowDimension === colDimension) return null;
    const f: DimensionCrossTabFilters = {
      row_dimension: rowDimension,
      col_dimension: colDimension,
      metric,
    };
    if (periodFrom && periodTo) {
      f.fiscal_year = fiscalYear;
      f.period_from = periodFrom;
      f.period_to = periodTo;
    }
    return f;
  }, [rowDimension, colDimension, metric, fiscalYear, periodFrom, periodTo]);

  const { data: report, isLoading, isError } = useDimensionCrossTab(filters);

  const metricLabel = metric === "revenue" ? "Revenue" : metric === "expenses" ? "Expenses" : "Net Income";

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Dimension Cross-Tab"
          subtitle="Cross-tabulation of amounts across two analysis dimensions"
        />

        {/* Filters */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-end gap-4">
              {/* Row dimension */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Row Dimension</label>
                <Select value={rowDimension} onValueChange={setRowDimension}>
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="Select..." />
                  </SelectTrigger>
                  <SelectContent>
                    {contextDimensions
                      .filter((d) => d.code !== colDimension)
                      .map((d) => (
                        <SelectItem key={d.code} value={d.code}>
                          {getText(d.name, d.name_ar)}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Column dimension */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Column Dimension</label>
                <Select value={colDimension} onValueChange={setColDimension}>
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="Select..." />
                  </SelectTrigger>
                  <SelectContent>
                    {contextDimensions
                      .filter((d) => d.code !== rowDimension)
                      .map((d) => (
                        <SelectItem key={d.code} value={d.code}>
                          {getText(d.name, d.name_ar)}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Metric */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Metric</label>
                <Select value={metric} onValueChange={setMetric}>
                  <SelectTrigger className="w-[150px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="net_income">Net Income</SelectItem>
                    <SelectItem value="revenue">Revenue</SelectItem>
                    <SelectItem value="expenses">Expenses</SelectItem>
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
                  <SelectTrigger className="w-[130px]">
                    <SelectValue placeholder="All" />
                  </SelectTrigger>
                  <SelectContent>
                    {periodOptions.map((p) => (
                      <SelectItem key={p.period} value={String(p.period)}>
                        P{p.period}
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
                  <SelectTrigger className="w-[130px]">
                    <SelectValue placeholder="All" />
                  </SelectTrigger>
                  <SelectContent>
                    {periodOptions.map((p) => (
                      <SelectItem key={p.period} value={String(p.period)}>
                        P{p.period}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

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
        {(!rowDimension || !colDimension) && (
          <EmptyState
            title="Select Two Dimensions"
            description="Choose row and column dimensions to generate a cross-tab report."
          />
        )}

        {rowDimension === colDimension && rowDimension && (
          <Card>
            <CardContent className="py-8 text-center text-muted-foreground">
              Row and column dimensions must be different.
            </CardContent>
          </Card>
        )}

        {isLoading && filters && <LoadingSpinner />}

        {isError && filters && (
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
                {getText(report.row_dimension.name, report.row_dimension.name_ar)}
                {" x "}
                {getText(report.col_dimension.name, report.col_dimension.name_ar)}
                <span className="text-sm font-normal text-muted-foreground ml-2">
                  ({metricLabel})
                </span>
                {report.date_from && report.date_to && (
                  <span className="text-sm font-normal text-muted-foreground ml-2">
                    | {report.date_from} to {report.date_to}
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {report.rows.length === 0 || report.columns.length === 0 ? (
                <div className="py-8 text-center text-muted-foreground">
                  No cross-tab data found. Ensure journal entries are tagged with both dimensions.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="sticky left-0 bg-background z-10 min-w-[120px]">
                          {getText(report.row_dimension.name, report.row_dimension.name_ar)}
                        </TableHead>
                        {report.columns.map((col) => (
                          <TableHead key={col.code} className="text-right min-w-[100px]">
                            <div className="text-xs">{col.code}</div>
                            <div className="text-xs font-normal text-muted-foreground truncate max-w-[120px]">
                              {getText(col.name, col.name_ar)}
                            </div>
                          </TableHead>
                        ))}
                        <TableHead className="text-right font-bold min-w-[100px]">
                          Total
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {report.rows.map((row) => (
                        <TableRow key={row.code}>
                          <TableCell className="sticky left-0 bg-background z-10 font-medium">
                            <div className="font-mono text-xs">{row.code}</div>
                            <div className="text-xs text-muted-foreground truncate max-w-[120px]">
                              {getText(row.name, row.name_ar)}
                            </div>
                          </TableCell>
                          {row.values.map((val, idx) => {
                            const num = parseFloat(val);
                            return (
                              <TableCell
                                key={idx}
                                className={cn(
                                  "text-right tabular-nums text-sm",
                                  num > 0 ? "text-green-600 dark:text-green-400" :
                                  num < 0 ? "text-red-600 dark:text-red-400" : "text-muted-foreground"
                                )}
                              >
                                {num !== 0 ? formatAmount(val) : "-"}
                              </TableCell>
                            );
                          })}
                          <TableCell
                            className={cn(
                              "text-right tabular-nums font-medium",
                              parseFloat(row.total) > 0 ? "text-green-600 dark:text-green-400" :
                              parseFloat(row.total) < 0 ? "text-red-600 dark:text-red-400" : ""
                            )}
                          >
                            {formatAmount(row.total)}
                          </TableCell>
                        </TableRow>
                      ))}

                      {/* Column totals row */}
                      <TableRow className="border-t-2 font-bold">
                        <TableCell className="sticky left-0 bg-background z-10">
                          Total
                        </TableCell>
                        {report.column_totals.map((val, idx) => {
                          const num = parseFloat(val);
                          return (
                            <TableCell
                              key={idx}
                              className={cn(
                                "text-right tabular-nums",
                                num > 0 ? "text-green-600 dark:text-green-400" :
                                num < 0 ? "text-red-600 dark:text-red-400" : ""
                              )}
                            >
                              {formatAmount(val)}
                            </TableCell>
                          );
                        })}
                        <TableCell
                          className={cn(
                            "text-right tabular-nums font-bold",
                            parseFloat(report.grand_total) > 0 ? "text-green-600 dark:text-green-400" :
                            parseFloat(report.grand_total) < 0 ? "text-red-600 dark:text-red-400" : ""
                          )}
                        >
                          {formatAmount(report.grand_total)}
                        </TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </div>
              )}

              {report.currency && (
                <p className="text-xs text-muted-foreground mt-4">
                  All amounts in {report.currency}
                </p>
              )}
            </CardContent>
          </Card>
        )}
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
