import { useState, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useQuery } from "@tanstack/react-query";
import { Printer, Filter, Plus, X } from "lucide-react";
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
import { Separator } from "@/components/ui/separator";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useBilingualText } from "@/components/common/BilingualText";
import { useIncomeStatement, usePeriodIncomeStatement } from "@/queries/useReports";
import { periodsService } from "@/services/periods.service";
import { dimensionsService } from "@/services/accounts.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import type { IncomeStatementSection, DimensionFilter } from "@/types/report";

interface DimensionFilterState {
  dimension_code: string;
  code_from: string;
  code_to: string;
}

export default function IncomeStatementPage() {
  const { t } = useTranslation(["common", "reports"]);
  const router = useRouter();
  const getText = useBilingualText();
  const { company } = useAuth();

  // Period filter state
  const currentYear = new Date().getFullYear();
  const [fiscalYear, setFiscalYear] = useState<number>(currentYear);
  const [periodFrom, setPeriodFrom] = useState<number | null>(null);
  const [periodTo, setPeriodTo] = useState<number | null>(null);
  const [filterApplied, setFilterApplied] = useState(false);

  // Dimension filter state
  const [dimensionFilters, setDimensionFilters] = useState<DimensionFilterState[]>([]);

  // Fetch available periods
  const { data: periodsData, isLoading: periodsLoading } = useQuery({
    queryKey: ["periods", fiscalYear],
    queryFn: async () => {
      const { data } = await periodsService.list(fiscalYear);
      return data;
    },
  });

  // Fetch available dimensions
  const { data: dimensions } = useQuery({
    queryKey: ["dimensions"],
    queryFn: async () => {
      const { data } = await dimensionsService.list();
      return data;
    },
  });

  // Build period options
  const periodOptions = useMemo(() => {
    if (!periodsData?.periods) return [];
    return periodsData.periods
      .filter((p) => p.fiscal_year === fiscalYear)
      .sort((a, b) => a.period - b.period);
  }, [periodsData, fiscalYear]);

  // Fiscal year options (last 5 years)
  const fiscalYearOptions = useMemo(() => {
    const years = [];
    for (let i = currentYear + 1; i >= currentYear - 4; i--) {
      years.push(i);
    }
    return years;
  }, [currentYear]);

  // Available dimensions for adding filters (exclude already selected)
  const availableDimensions = useMemo(() => {
    if (!dimensions) return [];
    const selectedCodes = new Set(dimensionFilters.map((df) => df.dimension_code));
    return dimensions.filter((d) => !selectedCodes.has(d.code));
  }, [dimensions, dimensionFilters]);

  // Determine which query to use
  const periodFiltersForQuery =
    filterApplied && periodFrom !== null && periodTo !== null
      ? {
          fiscal_year: fiscalYear,
          period_from: periodFrom,
          period_to: periodTo,
          dimension_filters: dimensionFilters.filter(
            (df) => df.dimension_code && (df.code_from || df.code_to)
          ),
        }
      : null;

  // Fetch income statement data
  const { data: defaultData, isLoading: defaultLoading } = useIncomeStatement();
  const { data: periodData, isLoading: periodLoading } =
    usePeriodIncomeStatement(periodFiltersForQuery);

  const isLoading = periodFiltersForQuery ? periodLoading : defaultLoading;
  const data = periodFiltersForQuery && periodData ? periodData : defaultData;

  const formatCurrency = (amount: string | number) => {
    const num = typeof amount === "string" ? parseFloat(amount) : amount;
    return new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(num);
  };

  const formatDate = (date: string) => {
    return new Date(date).toLocaleDateString(
      router.locale === "ar" ? "ar-SA" : "en-US",
      { year: "numeric", month: "long", day: "numeric" }
    );
  };

  const handlePrint = () => {
    window.print();
  };

  const handleApplyFilter = () => {
    if (periodFrom !== null && periodTo !== null) {
      setFilterApplied(true);
    }
  };

  const handleClearFilter = () => {
    setPeriodFrom(null);
    setPeriodTo(null);
    setDimensionFilters([]);
    setFilterApplied(false);
  };

  // When fiscal year changes, reset period selections
  const handleFiscalYearChange = (value: string) => {
    const year = parseInt(value, 10);
    setFiscalYear(year);
    setPeriodFrom(null);
    setPeriodTo(null);
    setFilterApplied(false);
  };

  // Add a new dimension filter
  const handleAddDimensionFilter = () => {
    if (availableDimensions.length > 0) {
      setDimensionFilters([
        ...dimensionFilters,
        { dimension_code: "", code_from: "", code_to: "" },
      ]);
    }
  };

  // Update a dimension filter
  const handleUpdateDimensionFilter = (
    index: number,
    field: keyof DimensionFilterState,
    value: string
  ) => {
    const updated = [...dimensionFilters];
    updated[index] = { ...updated[index], [field]: value };
    setDimensionFilters(updated);
  };

  // Remove a dimension filter
  const handleRemoveDimensionFilter = (index: number) => {
    setDimensionFilters(dimensionFilters.filter((_, i) => i !== index));
  };

  const renderSection = (section: IncomeStatementSection, title: string) => (
    <div className="space-y-2">
      <h4 className="font-bold text-lg">{title}</h4>
      <div className="space-y-1">
        {section.accounts.map((account) => (
          <div
            key={account.code}
            className={cn(
              "flex justify-between py-1",
              account.is_header && "font-semibold"
            )}
            style={{ paddingInlineStart: account.level * 16 }}
          >
            <span>
              <span className="font-mono text-xs me-2 text-muted-foreground ltr-code">
                {account.code}
              </span>
              {getText(account.name, account.name_ar)}
            </span>
            <span className="ltr-number">
              {!account.is_header && formatCurrency(account.amount)}
            </span>
          </div>
        ))}
      </div>
      <div className="flex justify-between font-bold border-t pt-2">
        <span>{getText(section.title, section.title_ar)}</span>
        <span className="ltr-number">{formatCurrency(section.total)}</span>
      </div>
    </div>
  );

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("reports:incomeStatement.title")}
          subtitle={t("reports:incomeStatement.subtitle")}
          actions={
            <Button variant="outline" onClick={handlePrint}>
              <Printer className="me-2 h-4 w-4" />
              {t("reports:actions.print")}
            </Button>
          }
        />

        {/* Period Filter Card */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-end gap-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">
                  {t("reports:filters.fiscalYear", "Fiscal Year")}
                </label>
                <Select
                  value={fiscalYear.toString()}
                  onValueChange={handleFiscalYearChange}
                >
                  <SelectTrigger className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {fiscalYearOptions.map((year) => (
                      <SelectItem key={year} value={year.toString()}>
                        {year}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">
                  {t("reports:filters.periodFrom", "Period From")}
                </label>
                <Select
                  value={periodFrom?.toString() ?? ""}
                  onValueChange={(v) => setPeriodFrom(parseInt(v, 10))}
                  disabled={periodsLoading || periodOptions.length === 0}
                >
                  <SelectTrigger className="w-40">
                    <SelectValue
                      placeholder={t("reports:filters.selectPeriod", "Select period")}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {periodOptions.map((p) => (
                      <SelectItem key={p.period} value={p.period.toString()}>
                        {t("reports:filters.periodLabel", "Period {{num}}", {
                          num: p.period,
                        })}{" "}
                        ({formatDate(p.start_date).split(",")[0]})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">
                  {t("reports:filters.periodTo", "Period To")}
                </label>
                <Select
                  value={periodTo?.toString() ?? ""}
                  onValueChange={(v) => setPeriodTo(parseInt(v, 10))}
                  disabled={periodsLoading || periodOptions.length === 0}
                >
                  <SelectTrigger className="w-40">
                    <SelectValue
                      placeholder={t("reports:filters.selectPeriod", "Select period")}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {periodOptions
                      .filter((p) => periodFrom === null || p.period >= periodFrom)
                      .map((p) => (
                        <SelectItem key={p.period} value={p.period.toString()}>
                          {t("reports:filters.periodLabel", "Period {{num}}", {
                            num: p.period,
                          })}{" "}
                          ({formatDate(p.end_date).split(",")[0]})
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex gap-2">
                <Button
                  onClick={handleApplyFilter}
                  disabled={periodFrom === null || periodTo === null}
                >
                  <Filter className="me-2 h-4 w-4" />
                  {t("reports:filters.apply", "Apply")}
                </Button>
                {filterApplied && (
                  <Button variant="outline" onClick={handleClearFilter}>
                    {t("reports:filters.clear", "Clear")}
                  </Button>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Dimension Filters Card */}
        {dimensions && dimensions.length > 0 && (
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">
                  {t("reports:filters.dimensionFilters", "Analysis Dimension Filters")}
                </CardTitle>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleAddDimensionFilter}
                  disabled={availableDimensions.length === 0}
                >
                  <Plus className="me-2 h-4 w-4" />
                  {t("reports:filters.addDimension", "Add Filter")}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {dimensionFilters.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("reports:filters.noDimensionFilters", "No dimension filters applied. Click 'Add Filter' to filter by analysis dimensions.")}
                </p>
              ) : (
                <div className="space-y-3">
                  {dimensionFilters.map((df, index) => {
                    // Get the selected dimension to populate value dropdowns
                    const selectedDimension = dimensions?.find(
                      (d) => d.code === df.dimension_code
                    );
                    const dimensionValues = selectedDimension?.values ?? [];
                    // Sort values by code for consistent ordering
                    const sortedValues = [...dimensionValues].sort((a, b) =>
                      a.code.localeCompare(b.code)
                    );

                    return (
                      <div key={index} className="flex flex-wrap items-end gap-3 p-3 border rounded-lg">
                        <div className="space-y-1 flex-1 min-w-[150px]">
                          <label className="text-xs font-medium text-muted-foreground">
                            {t("reports:filters.dimension", "Dimension")}
                          </label>
                          <Select
                            value={df.dimension_code}
                            onValueChange={(v) => {
                              // When dimension changes, reset the code_from and code_to
                              const updated = [...dimensionFilters];
                              updated[index] = {
                                dimension_code: v,
                                code_from: "",
                                code_to: "",
                              };
                              setDimensionFilters(updated);
                            }}
                          >
                            <SelectTrigger className="h-9">
                              <SelectValue
                                placeholder={t("reports:filters.selectDimension", "Select...")}
                              />
                            </SelectTrigger>
                            <SelectContent>
                              {dimensions
                                .filter(
                                  (d) =>
                                    d.code === df.dimension_code ||
                                    !dimensionFilters.some(
                                      (f, i) => i !== index && f.dimension_code === d.code
                                    )
                                )
                                .map((d) => (
                                  <SelectItem key={d.code} value={d.code}>
                                    {getText(d.name, d.name_ar)}
                                  </SelectItem>
                                ))}
                            </SelectContent>
                          </Select>
                        </div>

                        <div className="space-y-1 w-40">
                          <label className="text-xs font-medium text-muted-foreground">
                            {t("reports:filters.codeFrom", "Code From")}
                          </label>
                          <Select
                            value={df.code_from || "__all__"}
                            onValueChange={(v) =>
                              handleUpdateDimensionFilter(
                                index,
                                "code_from",
                                v === "__all__" ? "" : v
                              )
                            }
                            disabled={!df.dimension_code || sortedValues.length === 0}
                          >
                            <SelectTrigger className="h-9">
                              <SelectValue
                                placeholder={t("reports:filters.selectCode", "Select...")}
                              />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__all__">
                                {t("reports:filters.allCodes", "(All)")}
                              </SelectItem>
                              {sortedValues.map((v) => (
                                <SelectItem key={v.code} value={v.code}>
                                  {v.code} - {getText(v.name, v.name_ar)}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>

                        <div className="space-y-1 w-40">
                          <label className="text-xs font-medium text-muted-foreground">
                            {t("reports:filters.codeTo", "Code To")}
                          </label>
                          <Select
                            value={df.code_to || "__all__"}
                            onValueChange={(v) =>
                              handleUpdateDimensionFilter(
                                index,
                                "code_to",
                                v === "__all__" ? "" : v
                              )
                            }
                            disabled={!df.dimension_code || sortedValues.length === 0}
                          >
                            <SelectTrigger className="h-9">
                              <SelectValue
                                placeholder={t("reports:filters.selectCode", "Select...")}
                              />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__all__">
                                {t("reports:filters.allCodes", "(All)")}
                              </SelectItem>
                              {sortedValues
                                .filter(
                                  (v) => !df.code_from || v.code >= df.code_from
                                )
                                .map((v) => (
                                  <SelectItem key={v.code} value={v.code}>
                                    {v.code} - {getText(v.name, v.name_ar)}
                                  </SelectItem>
                                ))}
                            </SelectContent>
                          </Select>
                        </div>

                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-9 w-9"
                          onClick={() => handleRemoveDimensionFilter(index)}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="pt-6">
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : !data ? (
              <EmptyState title={t("messages.noData")} />
            ) : (
              <div className="max-w-2xl mx-auto">
                {/* Report Header */}
                <div className="text-center mb-6 print:mb-8">
                  <h2 className="text-xl font-bold">{company?.name}</h2>
                  <h3 className="text-lg mt-2">{t("reports:incomeStatement.printTitle")}</h3>
                  {data.fiscal_year ? (
                    <>
                      <p className="text-muted-foreground mt-1">
                        {t("reports:incomeStatement.periodRange", "Period {{from}} to {{to}}", {
                          from: data.period_from,
                          to: data.period_to,
                        })}
                        {" - "}
                        {t("reports:incomeStatement.fiscalYear", "Fiscal Year {{year}}", {
                          year: data.fiscal_year,
                        })}
                      </p>
                      {data.period_start_date && data.period_end_date && (
                        <p className="text-muted-foreground text-sm">
                          {formatDate(data.period_start_date)} - {formatDate(data.period_end_date)}
                        </p>
                      )}
                      {data.dimension_filters && data.dimension_filters.length > 0 && (
                        <p className="text-muted-foreground text-sm mt-1">
                          {t("reports:filters.filteredBy", "Filtered by")}:{" "}
                          {data.dimension_filters.map((df) => (
                            <span key={df.dimension_code} className="inline-block bg-muted px-2 py-0.5 rounded text-xs me-1">
                              {df.dimension_code}: {df.code_from || "*"} - {df.code_to || "*"}
                            </span>
                          ))}
                        </p>
                      )}
                    </>
                  ) : (
                    <p className="text-muted-foreground mt-1">
                      {t("reports:incomeStatement.periodFrom")}: {formatDate(data.period_from)}{" "}
                      {t("reports:incomeStatement.periodTo")} {formatDate(data.period_to)}
                    </p>
                  )}
                </div>

                <div className="space-y-8">
                  {/* Revenue */}
                  {renderSection(data.revenue, t("reports:incomeStatement.revenue"))}

                  <Separator />

                  {/* Expenses */}
                  {renderSection(data.expenses, t("reports:incomeStatement.expenses"))}

                  <Separator />

                  {/* Net Income */}
                  <div className="flex justify-between font-bold text-xl border-t-2 pt-4">
                    <span>
                      {data.is_profit
                        ? t("reports:incomeStatement.netIncome")
                        : t("reports:incomeStatement.netLoss")}
                    </span>
                    <span
                      className={cn(
                        "ltr-number",
                        data.is_profit ? "text-green-400" : "text-red-400"
                      )}
                    >
                      {formatCurrency(data.net_income)}
                    </span>
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
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
