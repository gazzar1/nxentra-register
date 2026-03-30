import { useState, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useQuery } from "@tanstack/react-query";
import { Printer } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useBilingualText } from "@/components/common/BilingualText";
import { useDimensionPLComparison } from "@/queries/useReports";
import { dimensionsService } from "@/services/accounts.service";
import { periodsService } from "@/services/periods.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import type {
  DimensionPLComparisonFilters,
  DimensionPLComparisonSection,
  DimensionPLComparisonAccount,
} from "@/types/report";

export default function DimensionPLComparisonPage() {
  const { t } = useTranslation(["common", "reports"]);
  const getText = useBilingualText();
  const { company } = useAuth();
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();

  const currentYear = new Date().getFullYear();
  const [dimensionCode, setDimensionCode] = useState<string>("");
  const [valueA, setValueA] = useState<string>("");
  const [valueB, setValueB] = useState<string>("");
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

  // Get selected dimension's values
  const selectedDimension = useMemo(
    () => contextDimensions.find((d) => d.code === dimensionCode),
    [contextDimensions, dimensionCode]
  );

  const dimensionValues = useMemo(() => {
    if (!selectedDimension?.values) return [];
    return [...selectedDimension.values].sort((a, b) =>
      a.code.localeCompare(b.code)
    );
  }, [selectedDimension]);

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
  const filters: DimensionPLComparisonFilters | null = useMemo(() => {
    if (
      !dimensionCode ||
      !valueA ||
      !valueB ||
      valueA === valueB ||
      periodFrom === null ||
      periodTo === null
    )
      return null;
    return {
      dimension_code: dimensionCode,
      value_a: valueA,
      value_b: valueB,
      fiscal_year: fiscalYear,
      period_from: periodFrom,
      period_to: periodTo,
    };
  }, [dimensionCode, valueA, valueB, fiscalYear, periodFrom, periodTo]);

  const { data: report, isLoading, isError } = useDimensionPLComparison(filters);

  const handleDimensionChange = (code: string) => {
    setDimensionCode(code);
    setValueA("");
    setValueB("");
  };

  const renderComparisonSection = (
    section: DimensionPLComparisonSection,
    sectionTitle: string
  ) => (
    <div className="space-y-1">
      <h4 className="font-bold text-lg mb-2">{sectionTitle}</h4>
      {section.accounts.map((acc: DimensionPLComparisonAccount) => {
        const varNum = parseFloat(acc.variance);
        return (
          <div
            key={acc.code}
            className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-4 py-1 text-sm"
          >
            <span>
              <span className="font-mono text-xs me-2 text-muted-foreground">
                {acc.code}
              </span>
              {getText(acc.name, acc.name_ar)}
            </span>
            <span className="text-right tabular-nums w-28">
              {formatAmount(acc.amount_a)}
            </span>
            <span className="text-right tabular-nums w-28">
              {formatAmount(acc.amount_b)}
            </span>
            <span
              className={cn(
                "text-right tabular-nums w-28",
                varNum > 0
                  ? "text-green-600 dark:text-green-400"
                  : varNum < 0
                    ? "text-red-600 dark:text-red-400"
                    : ""
              )}
            >
              {formatAmount(acc.variance)}
            </span>
            <span
              className={cn(
                "text-right tabular-nums w-20 text-xs",
                varNum > 0
                  ? "text-green-600 dark:text-green-400"
                  : varNum < 0
                    ? "text-red-600 dark:text-red-400"
                    : "text-muted-foreground"
              )}
            >
              {acc.variance_pct ? `${acc.variance_pct}%` : "-"}
            </span>
          </div>
        );
      })}
      {/* Section totals */}
      <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-4 py-2 font-bold border-t">
        <span>{getText(section.title, section.title_ar)}</span>
        <span className="text-right tabular-nums w-28">
          {formatAmount(section.total_a)}
        </span>
        <span className="text-right tabular-nums w-28">
          {formatAmount(section.total_b)}
        </span>
        <span
          className={cn(
            "text-right tabular-nums w-28",
            parseFloat(section.variance) > 0
              ? "text-green-600 dark:text-green-400"
              : parseFloat(section.variance) < 0
                ? "text-red-600 dark:text-red-400"
                : ""
          )}
        >
          {formatAmount(section.variance)}
        </span>
        <span
          className={cn(
            "text-right tabular-nums w-20 text-xs",
            parseFloat(section.variance) > 0
              ? "text-green-600 dark:text-green-400"
              : parseFloat(section.variance) < 0
                ? "text-red-600 dark:text-red-400"
                : "text-muted-foreground"
          )}
        >
          {section.variance_pct ? `${section.variance_pct}%` : "-"}
        </span>
      </div>
    </div>
  );

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Dimension P&L Comparison"
          subtitle="Side-by-side income statement comparison for two dimension values"
        />

        {/* Filters */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-end gap-4">
              {/* Dimension */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Dimension</label>
                <Select value={dimensionCode} onValueChange={handleDimensionChange}>
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="Select..." />
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

              {/* Value A */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Value A</label>
                <Select
                  value={valueA}
                  onValueChange={setValueA}
                  disabled={!dimensionCode}
                >
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="Select..." />
                  </SelectTrigger>
                  <SelectContent>
                    {dimensionValues
                      .filter((v) => v.code !== valueB)
                      .map((v) => (
                        <SelectItem key={v.code} value={v.code}>
                          {v.code} - {getText(v.name, v.name_ar)}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Value B */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Value B</label>
                <Select
                  value={valueB}
                  onValueChange={setValueB}
                  disabled={!dimensionCode}
                >
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="Select..." />
                  </SelectTrigger>
                  <SelectContent>
                    {dimensionValues
                      .filter((v) => v.code !== valueA)
                      .map((v) => (
                        <SelectItem key={v.code} value={v.code}>
                          {v.code} - {getText(v.name, v.name_ar)}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Fiscal Year */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Fiscal Year</label>
                <Select
                  value={String(fiscalYear)}
                  onValueChange={(v) => {
                    setFiscalYear(Number(v));
                    setPeriodFrom(null);
                    setPeriodTo(null);
                  }}
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

              {/* Period From */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Period From</label>
                <Select
                  value={periodFrom ? String(periodFrom) : ""}
                  onValueChange={(v) => setPeriodFrom(v ? Number(v) : null)}
                >
                  <SelectTrigger className="w-[130px]">
                    <SelectValue placeholder="Select..." />
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

              {/* Period To */}
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Period To</label>
                <Select
                  value={periodTo ? String(periodTo) : ""}
                  onValueChange={(v) => setPeriodTo(v ? Number(v) : null)}
                >
                  <SelectTrigger className="w-[130px]">
                    <SelectValue placeholder="Select..." />
                  </SelectTrigger>
                  <SelectContent>
                    {periodOptions
                      .filter((p) => periodFrom === null || p.period >= periodFrom)
                      .map((p) => (
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

        {/* Validation messages */}
        {(!dimensionCode || !valueA || !valueB) && (
          <EmptyState
            title="Select Dimension and Values"
            description="Choose a dimension and two values to compare their income statements side by side."
          />
        )}

        {valueA && valueB && valueA === valueB && (
          <Card>
            <CardContent className="py-8 text-center text-muted-foreground">
              Please select two different dimension values to compare.
            </CardContent>
          </Card>
        )}

        {dimensionCode && valueA && valueB && valueA !== valueB && (periodFrom === null || periodTo === null) && (
          <Card>
            <CardContent className="py-8 text-center text-muted-foreground">
              Select a period range to generate the comparison.
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

        {/* Report */}
        {report && (
          <Card className="print:shadow-none print:border-0">
            <CardContent className="pt-6 print:p-0">
              <div className="max-w-4xl mx-auto">
                {/* Report Header */}
                <div className="text-center mb-6 print:mb-8">
                  <h2 className="text-xl font-bold">{company?.name}</h2>
                  <h3 className="text-lg mt-2">
                    Income Statement Comparison
                  </h3>
                  <p className="text-muted-foreground mt-1">
                    {getText(report.dimension.name, report.dimension.name_ar)}:{" "}
                    <strong>{getText(report.value_a.name, report.value_a.name_ar)}</strong>
                    {" vs "}
                    <strong>{getText(report.value_b.name, report.value_b.name_ar)}</strong>
                  </p>
                  <p className="text-muted-foreground text-sm">
                    Fiscal Year {report.fiscal_year}, Periods {report.period_from}-{report.period_to}
                    {" | "}
                    {report.period_start_date} to {report.period_end_date}
                  </p>
                </div>

                {/* Column Headers */}
                <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-4 pb-2 border-b-2 text-sm font-medium text-muted-foreground">
                  <span>Account</span>
                  <span className="text-right w-28">
                    {report.value_a.code}
                  </span>
                  <span className="text-right w-28">
                    {report.value_b.code}
                  </span>
                  <span className="text-right w-28">Variance</span>
                  <span className="text-right w-20">%</span>
                </div>

                <div className="space-y-6 mt-4">
                  {/* Revenue */}
                  {renderComparisonSection(report.revenue, "Revenue")}

                  <Separator />

                  {/* Expenses */}
                  {renderComparisonSection(report.expenses, "Expenses")}

                  <Separator />

                  {/* Net Income */}
                  <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-4 py-3 font-bold text-lg border-t-2">
                    <span>
                      {report.is_profit_a || report.is_profit_b
                        ? "Net Income"
                        : "Net Loss"}
                    </span>
                    <span
                      className={cn(
                        "text-right tabular-nums w-28",
                        report.is_profit_a
                          ? "text-green-600 dark:text-green-400"
                          : "text-red-600 dark:text-red-400"
                      )}
                    >
                      {formatAmount(report.net_income_a)}
                    </span>
                    <span
                      className={cn(
                        "text-right tabular-nums w-28",
                        report.is_profit_b
                          ? "text-green-600 dark:text-green-400"
                          : "text-red-600 dark:text-red-400"
                      )}
                    >
                      {formatAmount(report.net_income_b)}
                    </span>
                    <span
                      className={cn(
                        "text-right tabular-nums w-28",
                        parseFloat(report.net_variance) > 0
                          ? "text-green-600 dark:text-green-400"
                          : parseFloat(report.net_variance) < 0
                            ? "text-red-600 dark:text-red-400"
                            : ""
                      )}
                    >
                      {formatAmount(report.net_variance)}
                    </span>
                    <span
                      className={cn(
                        "text-right tabular-nums w-20 text-sm",
                        parseFloat(report.net_variance) > 0
                          ? "text-green-600 dark:text-green-400"
                          : parseFloat(report.net_variance) < 0
                            ? "text-red-600 dark:text-red-400"
                            : "text-muted-foreground"
                      )}
                    >
                      {report.net_variance_pct
                        ? `${report.net_variance_pct}%`
                        : "-"}
                    </span>
                  </div>
                </div>

                {report.currency && (
                  <p className="text-xs text-muted-foreground mt-4">
                    All amounts in {report.currency}
                  </p>
                )}
              </div>
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
