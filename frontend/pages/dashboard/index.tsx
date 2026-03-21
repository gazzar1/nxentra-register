import { useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import {
  BookOpen,
  FileText,
  BarChart3,
  TrendingUp,
  TrendingDown,
  DollarSign,
  Landmark,
  AlertTriangle,
  Activity,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useModules } from "@/queries/useModules";
import { useTrialBalance, useDashboardCharts, useDashboardWidgets } from "@/queries/useReports";
import { useJournalEntries } from "@/queries/useJournalEntries";
import {
  RevenueExpensesChart,
  AccountDistributionChart,
  NetIncomeTrendChart,
  TopAccountsChart,
} from "@/components/charts";

const ONBOARDING_DONE_KEY = "nxentra-onboarding-modules-done";

export default function DashboardPage() {
  const { t } = useTranslation(["common", "reports"]);
  const { company, membership } = useAuth();
  const router = useRouter();
  const { data: modules } = useModules();

  // Redirect new OWNER users to module onboarding if no optional modules enabled
  useEffect(() => {
    if (!modules || !membership) return;
    // Only redirect OWNER (the person who registered the company)
    if (membership.role !== "OWNER") return;
    // Check if onboarding was already completed/dismissed
    try {
      if (sessionStorage.getItem(ONBOARDING_DONE_KEY)) return;
    } catch {}
    // If any optional module is enabled, skip onboarding
    const hasOptional = modules.some((m) => !m.is_core && m.is_enabled);
    if (hasOptional) {
      try { sessionStorage.setItem(ONBOARDING_DONE_KEY, "1"); } catch {}
      return;
    }
    router.replace("/onboarding/modules");
  }, [modules, membership, router]);
  const { data: trialBalance } = useTrialBalance();
  const { data: recentEntries } = useJournalEntries({ status: "POSTED" });
  const { data: chartData, isLoading: chartsLoading } = useDashboardCharts();
  const { data: widgets } = useDashboardWidgets();

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

        {/* Cash Position & AR Overdue */}
        {widgets && (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {/* Cash Position */}
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Cash Position</CardTitle>
                <Landmark className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold ltr-number text-blue-500">
                  {formatCurrency(parseFloat(widgets.cash_position.total))}
                </div>
                <div className="mt-3 space-y-1.5">
                  {widgets.cash_position.accounts.map((acc) => (
                    <div key={acc.code} className="flex justify-between text-sm">
                      <span className="text-muted-foreground truncate mr-2">
                        <span className="font-mono">{acc.code}</span> {acc.name}
                      </span>
                      <span className="font-mono ltr-number whitespace-nowrap">
                        {formatCurrency(parseFloat(acc.balance))}
                      </span>
                    </div>
                  ))}
                  {widgets.cash_position.accounts.length === 0 && (
                    <p className="text-sm text-muted-foreground">No cash accounts</p>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* AR Overdue */}
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Overdue Receivables</CardTitle>
                <AlertTriangle className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold ltr-number text-orange-500">
                  {formatCurrency(parseFloat(widgets.ar_overdue.overdue_total))}
                </div>
                <p className="text-xs text-muted-foreground mb-3">
                  {widgets.ar_overdue.customer_count} customer{widgets.ar_overdue.customer_count !== 1 ? "s" : ""} overdue
                </p>
                <div className="space-y-1.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Current (0-30d)</span>
                    <span className="font-mono ltr-number">{formatCurrency(parseFloat(widgets.ar_overdue.current))}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">31-60 days</span>
                    <span className="font-mono ltr-number text-yellow-500">{formatCurrency(parseFloat(widgets.ar_overdue.days_31_60))}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">61-90 days</span>
                    <span className="font-mono ltr-number text-orange-500">{formatCurrency(parseFloat(widgets.ar_overdue.days_61_90))}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Over 90 days</span>
                    <span className="font-mono ltr-number text-red-500">{formatCurrency(parseFloat(widgets.ar_overdue.over_90))}</span>
                  </div>
                  <div className="flex justify-between pt-1.5 border-t font-medium">
                    <span>Total AR</span>
                    <span className="font-mono ltr-number">{formatCurrency(parseFloat(widgets.ar_overdue.total))}</span>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Recent Activity */}
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Recent Activity</CardTitle>
                <Activity className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {widgets.recent_activity.slice(0, 6).map((item, i) => (
                    <div key={i} className="flex items-start justify-between gap-2 text-sm">
                      <div className="min-w-0 flex-1">
                        <p className="font-mono text-xs text-muted-foreground">
                          {item.entry_number}
                        </p>
                        <p className="truncate text-muted-foreground">
                          {item.memo || item.source}
                        </p>
                      </div>
                      <div className="text-right whitespace-nowrap">
                        <p className="font-mono ltr-number text-xs">
                          {formatCurrency(parseFloat(item.amount))}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {new Date(item.date).toLocaleDateString()}
                        </p>
                      </div>
                    </div>
                  ))}
                  {widgets.recent_activity.length === 0 && (
                    <p className="text-sm text-muted-foreground text-center py-4">No recent activity</p>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        )}

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
