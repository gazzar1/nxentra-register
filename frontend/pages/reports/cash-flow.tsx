import { useState, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useQuery } from "@tanstack/react-query";
import { Printer, Filter } from "lucide-react";
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
import { periodsService } from "@/services/periods.service";
import { reportsService } from "@/services/reports.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";

interface CashFlowItem {
  code: string;
  name: string;
  name_ar: string;
  amount: string;
}

interface CashFlowSection {
  title: string;
  title_ar: string;
  items?: CashFlowItem[];
  net_income?: string;
  adjustments?: CashFlowItem[];
  total_adjustments?: string;
  net_cash?: string;
  total?: string;
}

interface CashFlowData {
  fiscal_year: number;
  period_from: number;
  period_to: number;
  start_date: string;
  end_date: string;
  operating_activities: CashFlowSection;
  investing_activities: CashFlowSection;
  financing_activities: CashFlowSection;
  net_change_in_cash: string;
  beginning_cash: string;
  ending_cash: string;
}

export default function CashFlowStatementPage() {
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

  // Fetch available periods
  const { data: periodsData, isLoading: periodsLoading } = useQuery({
    queryKey: ["periods", fiscalYear],
    queryFn: async () => {
      const { data } = await periodsService.list(fiscalYear);
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

  // Fiscal year options
  const fiscalYearOptions = useMemo(() => {
    const years = [];
    for (let i = currentYear + 1; i >= currentYear - 4; i--) {
      years.push(i);
    }
    return years;
  }, [currentYear]);

  // Fetch cash flow statement data
  const { data, isLoading } = useQuery<CashFlowData>({
    queryKey: [
      "cash-flow-statement",
      fiscalYear,
      filterApplied ? periodFrom : null,
      filterApplied ? periodTo : null,
    ],
    queryFn: async () => {
      const params: Record<string, string> = {
        fiscal_year: fiscalYear.toString(),
      };
      if (filterApplied && periodFrom !== null && periodTo !== null) {
        params.period_from = periodFrom.toString();
        params.period_to = periodTo.toString();
      }
      const { data } = await reportsService.getCashFlowStatement(params);
      return data;
    },
  });

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
    setFilterApplied(false);
  };

  const handleFiscalYearChange = (value: string) => {
    const year = parseInt(value, 10);
    setFiscalYear(year);
    setPeriodFrom(null);
    setPeriodTo(null);
    setFilterApplied(false);
  };

  const renderActivityItem = (item: CashFlowItem) => (
    <div key={item.code} className="flex justify-between py-1 ps-4">
      <span>
        <span className="font-mono text-xs me-2 text-muted-foreground ltr-code">
          {item.code}
        </span>
        {getText(item.name, item.name_ar)}
      </span>
      <span className={cn("ltr-number", parseFloat(item.amount) < 0 && "text-red-600")}>
        {formatCurrency(item.amount)}
      </span>
    </div>
  );

  return (
    <AppLayout>
      <div className="space-y-6 print:space-y-0">
        <div className="no-print">
          <PageHeader
            title={t("reports:cashFlowStatement.title", "Cash Flow Statement")}
            subtitle={t("reports:cashFlowStatement.subtitle", "Statement of cash flows using the indirect method")}
            actions={
              <Button variant="outline" onClick={handlePrint}>
                <Printer className="me-2 h-4 w-4" />
                {t("reports:actions.print")}
              </Button>
            }
          />
        </div>

        {/* Period Filter Card */}
        <Card className="no-print">
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
                        })}
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
                          })}
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

        {/* Report Content */}
        {isLoading ? (
          <Card>
            <CardContent className="py-12">
              <LoadingSpinner />
            </CardContent>
          </Card>
        ) : !data ? (
          <Card>
            <CardContent className="py-12">
              <EmptyState
                title={t("reports:cashFlowStatement.noData", "No cash flow data")}
                description={t("reports:cashFlowStatement.noDataDescription", "Select a period range to generate the cash flow statement")}
              />
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader className="text-center print:pb-8">
              <CardTitle className="text-xl">
                {company?.name}
              </CardTitle>
              <p className="text-lg font-semibold">
                {t("reports:cashFlowStatement.title", "Cash Flow Statement")}
              </p>
              <p className="text-sm text-muted-foreground">
                {t("reports:cashFlowStatement.periodRange", "For the period {{from}} to {{to}}", {
                  from: formatDate(data.start_date),
                  to: formatDate(data.end_date),
                })}
              </p>
            </CardHeader>
            <CardContent className="space-y-8">
              {/* Operating Activities */}
              <div className="space-y-3">
                <h3 className="font-bold text-lg">
                  {getText(data.operating_activities.title, data.operating_activities.title_ar)}
                </h3>

                <div className="flex justify-between py-1">
                  <span className="ps-4">{t("reports:cashFlowStatement.netIncome", "Net Income")}</span>
                  <span className="ltr-number font-semibold">
                    {formatCurrency(data.operating_activities.net_income || "0")}
                  </span>
                </div>

                {data.operating_activities.adjustments && data.operating_activities.adjustments.length > 0 && (
                  <div className="space-y-1">
                    <p className="text-sm text-muted-foreground ps-4">
                      {t("reports:cashFlowStatement.adjustments", "Adjustments for non-cash items and working capital changes:")}
                    </p>
                    {data.operating_activities.adjustments.map(renderActivityItem)}
                    <div className="flex justify-between py-1 ps-4 border-t">
                      <span className="font-medium">
                        {t("reports:cashFlowStatement.totalAdjustments", "Total Adjustments")}
                      </span>
                      <span className="ltr-number font-medium">
                        {formatCurrency(data.operating_activities.total_adjustments || "0")}
                      </span>
                    </div>
                  </div>
                )}

                <div className="flex justify-between py-2 border-t border-b font-bold bg-muted/50 px-4 -mx-4">
                  <span>{t("reports:cashFlowStatement.netCashOperating", "Net Cash from Operating Activities")}</span>
                  <span className="ltr-number">
                    {formatCurrency(data.operating_activities.net_cash || "0")}
                  </span>
                </div>
              </div>

              <Separator />

              {/* Investing Activities */}
              <div className="space-y-3">
                <h3 className="font-bold text-lg">
                  {getText(data.investing_activities.title, data.investing_activities.title_ar)}
                </h3>

                {data.investing_activities.items && data.investing_activities.items.length > 0 ? (
                  <div className="space-y-1">
                    {data.investing_activities.items.map(renderActivityItem)}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground ps-4">
                    {t("reports:cashFlowStatement.noInvestingActivities", "No investing activities in this period")}
                  </p>
                )}

                <div className="flex justify-between py-2 border-t border-b font-bold bg-muted/50 px-4 -mx-4">
                  <span>{t("reports:cashFlowStatement.netCashInvesting", "Net Cash from Investing Activities")}</span>
                  <span className="ltr-number">
                    {formatCurrency(data.investing_activities.total || "0")}
                  </span>
                </div>
              </div>

              <Separator />

              {/* Financing Activities */}
              <div className="space-y-3">
                <h3 className="font-bold text-lg">
                  {getText(data.financing_activities.title, data.financing_activities.title_ar)}
                </h3>

                {data.financing_activities.items && data.financing_activities.items.length > 0 ? (
                  <div className="space-y-1">
                    {data.financing_activities.items.map(renderActivityItem)}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground ps-4">
                    {t("reports:cashFlowStatement.noFinancingActivities", "No financing activities in this period")}
                  </p>
                )}

                <div className="flex justify-between py-2 border-t border-b font-bold bg-muted/50 px-4 -mx-4">
                  <span>{t("reports:cashFlowStatement.netCashFinancing", "Net Cash from Financing Activities")}</span>
                  <span className="ltr-number">
                    {formatCurrency(data.financing_activities.total || "0")}
                  </span>
                </div>
              </div>

              <Separator />

              {/* Summary */}
              <div className="space-y-3 bg-accent/20 p-4 rounded-lg">
                <div className="flex justify-between py-1 font-bold text-lg">
                  <span>{t("reports:cashFlowStatement.netChangeInCash", "Net Change in Cash")}</span>
                  <span className={cn("ltr-number", parseFloat(data.net_change_in_cash) < 0 && "text-red-600")}>
                    {formatCurrency(data.net_change_in_cash)}
                  </span>
                </div>

                <div className="flex justify-between py-1">
                  <span>{t("reports:cashFlowStatement.beginningCash", "Cash at Beginning of Period")}</span>
                  <span className="ltr-number">
                    {formatCurrency(data.beginning_cash)}
                  </span>
                </div>

                <div className="flex justify-between py-2 border-t font-bold text-lg">
                  <span>{t("reports:cashFlowStatement.endingCash", "Cash at End of Period")}</span>
                  <span className="ltr-number">
                    {formatCurrency(data.ending_cash)}
                  </span>
                </div>
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
