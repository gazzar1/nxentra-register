import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import {
  BookOpen,
  FileText,
  BarChart3,
  TrendingUp,
  TrendingDown,
  DollarSign,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useTrialBalance, useDashboardCharts } from "@/queries/useReports";
import { useJournalEntries } from "@/queries/useJournalEntries";
import {
  RevenueExpensesChart,
  AccountDistributionChart,
  NetIncomeTrendChart,
  TopAccountsChart,
} from "@/components/charts";

export default function DashboardPage() {
  const { t } = useTranslation(["common", "reports"]);
  const { company } = useAuth();
  const { data: trialBalance } = useTrialBalance();
  const { data: recentEntries } = useJournalEntries({ status: "POSTED" });
  const { data: chartData, isLoading: chartsLoading } = useDashboardCharts();

  // Calculate summary from trial balance
  const totalAssets = trialBalance?.accounts
    .filter((a) => a.account_type === "ASSET")
    .reduce((sum, a) => sum + parseFloat(a.debit || "0"), 0) || 0;

  const totalLiabilities = trialBalance?.accounts
    .filter((a) => a.account_type === "LIABILITY")
    .reduce((sum, a) => sum + parseFloat(a.credit || "0"), 0) || 0;

  const totalRevenue = trialBalance?.accounts
    .filter((a) => a.account_type === "REVENUE")
    .reduce((sum, a) => sum + parseFloat(a.credit || "0"), 0) || 0;

  const totalExpenses = trialBalance?.accounts
    .filter((a) => a.account_type === "EXPENSE")
    .reduce((sum, a) => sum + parseFloat(a.debit || "0"), 0) || 0;

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: company?.default_currency || "USD",
      minimumFractionDigits: 2,
    }).format(amount);
  };

  const quickActions = [
    {
      label: t("nav.chartOfAccounts"),
      href: "/accounting/chart-of-accounts",
      icon: <BookOpen className="h-5 w-5" />,
    },
    {
      label: t("nav.journalEntries"),
      href: "/accounting/journal-entries",
      icon: <FileText className="h-5 w-5" />,
    },
    {
      label: t("nav.trialBalance"),
      href: "/reports/trial-balance",
      icon: <BarChart3 className="h-5 w-5" />,
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("nav.dashboard")}
          subtitle={`${t("app.tagline")} - ${company?.name || ""}`}
        />

        {/* Summary Cards */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                {t("reports:balanceSheet.totalAssets", "Total Assets")}
              </CardTitle>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold ltr-number">
                {formatCurrency(totalAssets)}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                {t("reports:balanceSheet.totalLiabilities", "Total Liabilities")}
              </CardTitle>
              <TrendingDown className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold ltr-number">
                {formatCurrency(totalLiabilities)}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                {t("reports:incomeStatement.totalRevenue", "Total Revenue")}
              </CardTitle>
              <DollarSign className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold ltr-number text-green-400">
                {formatCurrency(totalRevenue)}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                {t("reports:incomeStatement.totalExpenses", "Total Expenses")}
              </CardTitle>
              <DollarSign className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold ltr-number text-red-400">
                {formatCurrency(totalExpenses)}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Charts - All 4 in one horizontal row */}
        {chartsLoading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : chartData ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <RevenueExpensesChart data={chartData.monthly_revenue_expenses} compact />
            <AccountDistributionChart data={chartData.account_type_distribution} compact />
            <NetIncomeTrendChart data={chartData.monthly_net_income} compact />
            <TopAccountsChart data={chartData.top_accounts} compact />
          </div>
        ) : null}

        {/* Quick Actions & Recent Entries */}
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Quick Actions */}
          <Card>
            <CardHeader>
              <CardTitle>{t("actions.create", "Quick Actions")}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-2">
                {quickActions.map((action) => (
                  <Link key={action.href} href={action.href}>
                    <Button
                      variant="outline"
                      className="w-full justify-start gap-2"
                    >
                      {action.icon}
                      {action.label}
                    </Button>
                  </Link>
                ))}
                <Link href="/accounting/journal-entries/new">
                  <Button className="w-full mt-2">
                    {t("accounting:journalEntries.createEntry", "New Journal Entry")}
                  </Button>
                </Link>
              </div>
            </CardContent>
          </Card>

          {/* Recent Journal Entries */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>{t("nav.journalEntries")}</CardTitle>
              <Link href="/accounting/journal-entries">
                <Button variant="ghost" size="sm">
                  {t("actions.view")}
                </Button>
              </Link>
            </CardHeader>
            <CardContent>
              {recentEntries && recentEntries.length > 0 ? (
                <div className="space-y-2">
                  {recentEntries.slice(0, 5).map((entry) => (
                    <Link
                      key={entry.id}
                      href={`/accounting/journal-entries/${entry.id}`}
                      className="flex items-center justify-between rounded-lg border p-3 hover:bg-muted"
                    >
                      <div>
                        <p className="font-medium ltr-code">
                          {entry.entry_number || `#${entry.id}`}
                        </p>
                        <p className="text-sm text-muted-foreground">
                          {entry.memo || entry.date}
                        </p>
                      </div>
                      <div className="text-end">
                        <p className="font-medium ltr-number">
                          {formatCurrency(parseFloat(entry.total_debit))}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {new Date(entry.date).toLocaleDateString()}
                        </p>
                      </div>
                    </Link>
                  ))}
                </div>
              ) : (
                <p className="text-center text-muted-foreground py-8">
                  {t("messages.noData")}
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", [
        "common",
        "accounting",
        "reports",
      ])),
    },
  };
};
