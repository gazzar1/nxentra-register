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
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useBilingualText } from "@/components/common/BilingualText";
import { useTrialBalance, usePeriodTrialBalance } from "@/queries/useReports";
import { periodsService } from "@/services/periods.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";

export default function TrialBalancePage() {
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

  // Fetch trial balance data
  const { data: defaultData, isLoading: defaultLoading } = useTrialBalance();
  const { data: periodData, isLoading: periodLoading } =
    usePeriodTrialBalance(periodFilters);

  const isLoading = periodFilters ? periodLoading : defaultLoading;
  const isPeriodView = !!periodFilters && !!periodData;

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

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("reports:trialBalance.title")}
          subtitle={t("reports:trialBalance.subtitle")}
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

        {/* Trial Balance Table */}
        <Card>
          <CardContent className="pt-6">
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : isPeriodView && periodData ? (
              // Period-filtered view
              <>
                {/* Report Header */}
                <div className="text-center mb-6 print:mb-8">
                  <h2 className="text-xl font-bold">{company?.name}</h2>
                  <h3 className="text-lg mt-2">
                    {t("reports:trialBalance.printTitle")}
                  </h3>
                  <p className="text-muted-foreground mt-1">
                    {t("reports:trialBalance.periodRange", "Period {{from}} to {{to}}", {
                      from: periodData.period_from,
                      to: periodData.period_to,
                    })}
                    {" - "}
                    {t("reports:trialBalance.fiscalYear", "Fiscal Year {{year}}", {
                      year: periodData.fiscal_year,
                    })}
                  </p>
                  <p className="text-muted-foreground text-sm">
                    {formatDate(periodData.period_start_date)} -{" "}
                    {formatDate(periodData.period_end_date)}
                  </p>
                </div>

                {periodData.accounts.length === 0 ? (
                  <EmptyState title={t("messages.noData")} />
                ) : (
                  <>
                    {/* Period Trial Balance Table */}
                    <div className="overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="w-24">
                              {t("reports:columns.code")}
                            </TableHead>
                            <TableHead>
                              {t("reports:columns.accountName")}
                            </TableHead>
                            <TableHead className="w-28">
                              {t("reports:columns.accountType", "Type")}
                            </TableHead>
                            <TableHead className="text-end w-32">
                              {t("reports:columns.openingBalance", "Opening Balance")}
                            </TableHead>
                            <TableHead className="text-end w-32">
                              {t("reports:columns.periodDebit", "Period Debits")}
                            </TableHead>
                            <TableHead className="text-end w-32">
                              {t("reports:columns.periodCredit", "Period Credits")}
                            </TableHead>
                            <TableHead className="text-end w-32">
                              {t("reports:columns.closingBalance", "Closing Balance")}
                            </TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {periodData.accounts.map((account) => (
                            <TableRow key={account.code}>
                              <TableCell className="font-mono ltr-code">
                                {account.code}
                              </TableCell>
                              <TableCell>
                                {getText(account.name, account.name_ar)}
                              </TableCell>
                              <TableCell className="text-sm text-muted-foreground">
                                {account.account_type}
                              </TableCell>
                              <TableCell className="text-end ltr-number">
                                {formatCurrency(account.opening_balance)}
                              </TableCell>
                              <TableCell className="text-end ltr-number">
                                {parseFloat(account.period_debit) > 0
                                  ? formatCurrency(account.period_debit)
                                  : "-"}
                              </TableCell>
                              <TableCell className="text-end ltr-number">
                                {parseFloat(account.period_credit) > 0
                                  ? formatCurrency(account.period_credit)
                                  : "-"}
                              </TableCell>
                              <TableCell className="text-end ltr-number">
                                {formatCurrency(account.closing_balance)}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                        <TableFooter>
                          <TableRow className="font-bold">
                            <TableCell colSpan={3}>
                              {t("reports:totals.label")}
                            </TableCell>
                            <TableCell className="text-end ltr-number">
                              {formatCurrency(periodData.totals.opening_balance)}
                            </TableCell>
                            <TableCell className="text-end ltr-number">
                              {formatCurrency(periodData.totals.period_debit)}
                            </TableCell>
                            <TableCell className="text-end ltr-number">
                              {formatCurrency(periodData.totals.period_credit)}
                            </TableCell>
                            <TableCell className="text-end ltr-number">
                              {formatCurrency(periodData.totals.closing_balance)}
                            </TableCell>
                          </TableRow>
                        </TableFooter>
                      </Table>
                    </div>

                    {/* Balance Check */}
                    <div className="mt-4 text-end">
                      <span
                        className={cn(
                          "text-sm font-medium",
                          periodData.is_balanced
                            ? "text-green-400"
                            : "text-red-400"
                        )}
                      >
                        {periodData.is_balanced
                          ? t("reports:balance.balanced", "Balanced")
                          : t("reports:balance.notBalanced", "Not Balanced")}
                      </span>
                    </div>
                  </>
                )}
              </>
            ) : !defaultData || defaultData.accounts.length === 0 ? (
              <EmptyState title={t("messages.noData")} />
            ) : (
              // Default view (no period filter)
              <>
                {/* Report Header */}
                <div className="text-center mb-6 print:mb-8">
                  <h2 className="text-xl font-bold">{company?.name}</h2>
                  <h3 className="text-lg mt-2">
                    {t("reports:trialBalance.printTitle")}
                  </h3>
                  <p className="text-muted-foreground mt-1">
                    {t("reports:trialBalance.asOf")}:{" "}
                    {formatDate(defaultData.as_of_date)}
                  </p>
                </div>

                {/* Default Table */}
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-24">
                        {t("reports:columns.code")}
                      </TableHead>
                      <TableHead>{t("reports:columns.accountName")}</TableHead>
                      <TableHead className="text-end w-36">
                        {t("reports:columns.debit")}
                      </TableHead>
                      <TableHead className="text-end w-36">
                        {t("reports:columns.credit")}
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {defaultData.accounts.map((account) => {
                      const debit = parseFloat(account.debit || "0");
                      const credit = parseFloat(account.credit || "0");
                      if (debit === 0 && credit === 0) return null;

                      return (
                        <TableRow key={account.code}>
                          <TableCell className="font-mono ltr-code">
                            {account.code}
                          </TableCell>
                          <TableCell>
                            {getText(account.name, account.name_ar)}
                          </TableCell>
                          <TableCell className="text-end ltr-number">
                            {debit > 0 ? formatCurrency(debit) : "-"}
                          </TableCell>
                          <TableCell className="text-end ltr-number">
                            {credit > 0 ? formatCurrency(credit) : "-"}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                  <TableFooter>
                    <TableRow className="font-bold">
                      <TableCell colSpan={2}>{t("reports:totals.label")}</TableCell>
                      <TableCell className="text-end ltr-number">
                        {formatCurrency(defaultData.total_debit)}
                      </TableCell>
                      <TableCell className="text-end ltr-number">
                        {formatCurrency(defaultData.total_credit)}
                      </TableCell>
                    </TableRow>
                  </TableFooter>
                </Table>

                {/* Balance Check */}
                <div className="mt-4 text-end">
                  <span
                    className={cn(
                      "text-sm font-medium",
                      defaultData.is_balanced ? "text-green-400" : "text-red-400"
                    )}
                  >
                    {defaultData.is_balanced
                      ? t("reports:balance.balanced", "Balanced")
                      : t("reports:balance.notBalanced", "Not Balanced")}
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
