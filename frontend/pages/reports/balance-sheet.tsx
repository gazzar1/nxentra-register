import { useState, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useQuery } from "@tanstack/react-query";
import { Printer, Filter } from "lucide-react";
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
import { useBalanceSheet, usePeriodBalanceSheet } from "@/queries/useReports";
import { periodsService } from "@/services/periods.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import type { BalanceSheetSection } from "@/types/report";

export default function BalanceSheetPage() {
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

  // Fiscal year options (last 5 years)
  const fiscalYearOptions = useMemo(() => {
    const years = [];
    for (let i = currentYear + 1; i >= currentYear - 4; i--) {
      years.push(i);
    }
    return years;
  }, [currentYear]);

  // Determine which query to use
  const periodFilters =
    filterApplied && periodFrom !== null && periodTo !== null
      ? { fiscal_year: fiscalYear, period_from: periodFrom, period_to: periodTo }
      : null;

  // Fetch balance sheet data
  const { data: defaultData, isLoading: defaultLoading } = useBalanceSheet();
  const { data: periodData, isLoading: periodLoading } =
    usePeriodBalanceSheet(periodFilters);

  const isLoading = periodFilters ? periodLoading : defaultLoading;
  const data = periodFilters && periodData ? periodData : defaultData;

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

  // When fiscal year changes, reset period selections
  const handleFiscalYearChange = (value: string) => {
    const year = parseInt(value, 10);
    setFiscalYear(year);
    setPeriodFrom(null);
    setPeriodTo(null);
    setFilterApplied(false);
  };

  const renderSection = (section: BalanceSheetSection, title: string) => (
    <div className="space-y-2">
      <h4 className="font-bold text-lg">{title}</h4>
      <div className="space-y-1">
        {section.accounts.map((account) => (
          <div
            key={account.code}
            className={cn(
              "flex justify-between py-1",
              account.is_header && "font-semibold",
              account.level > 0 && "ps-4"
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
              {!account.is_header && formatCurrency(account.balance)}
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
          title={t("reports:balanceSheet.title")}
          subtitle={t("reports:balanceSheet.subtitle")}
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

        <Card>
          <CardContent className="pt-6">
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : !data ? (
              <EmptyState title={t("messages.noData")} />
            ) : (
              <>
                {/* Report Header */}
                <div className="text-center mb-6 print:mb-8">
                  <h2 className="text-xl font-bold">{company?.name}</h2>
                  <h3 className="text-lg mt-2">{t("reports:balanceSheet.printTitle")}</h3>
                  {data.period_from && data.period_to ? (
                    <>
                      <p className="text-muted-foreground mt-1">
                        {t("reports:balanceSheet.periodRange", "Period {{from}} to {{to}}", {
                          from: data.period_from,
                          to: data.period_to,
                        })}
                        {" - "}
                        {t("reports:balanceSheet.fiscalYear", "Fiscal Year {{year}}", {
                          year: data.fiscal_year,
                        })}
                      </p>
                      <p className="text-muted-foreground text-sm">
                        {t("reports:balanceSheet.asOf")}: {formatDate(data.as_of_date)}
                      </p>
                    </>
                  ) : (
                    <p className="text-muted-foreground mt-1">
                      {t("reports:balanceSheet.asOf")}: {formatDate(data.as_of_date)}
                    </p>
                  )}
                </div>

                <div className="grid gap-8 lg:grid-cols-2">
                  {/* Assets */}
                  <div>
                    {renderSection(data.assets, t("reports:balanceSheet.assets"))}
                    <div className="mt-4 flex justify-between font-bold text-lg border-t-2 pt-2">
                      <span>{t("reports:balanceSheet.totalAssets")}</span>
                      <span className="ltr-number">{formatCurrency(data.total_assets)}</span>
                    </div>
                  </div>

                  {/* Liabilities & Equity */}
                  <div className="space-y-8">
                    {renderSection(data.liabilities, t("reports:balanceSheet.liabilities"))}

                    <Separator />

                    {renderSection(data.equity, t("reports:balanceSheet.equity"))}

                    <div className="flex justify-between font-bold text-lg border-t-2 pt-2">
                      <span>{t("reports:balanceSheet.totalLiabilitiesAndEquity")}</span>
                      <span className="ltr-number">
                        {formatCurrency(data.total_liabilities_and_equity)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Balance Check */}
                <div className="mt-6 text-center">
                  <span
                    className={cn(
                      "text-sm font-medium",
                      data.is_balanced ? "text-green-400" : "text-red-400"
                    )}
                  >
                    {data.is_balanced ? "Assets = Liabilities + Equity" : "Out of Balance"}
                  </span>
                </div>
              </>
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
