import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { BarChart3, FileSpreadsheet, PieChart } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";

export default function ReportsIndexPage() {
  const { t } = useTranslation(["common", "reports"]);

  const reports = [
    {
      title: t("reports:trialBalance.title"),
      description: t("reports:trialBalance.subtitle"),
      href: "/reports/trial-balance",
      icon: <FileSpreadsheet className="h-8 w-8" />,
    },
    {
      title: t("reports:balanceSheet.title"),
      description: t("reports:balanceSheet.subtitle"),
      href: "/reports/balance-sheet",
      icon: <BarChart3 className="h-8 w-8" />,
    },
    {
      title: t("reports:incomeStatement.title"),
      description: t("reports:incomeStatement.subtitle"),
      href: "/reports/income-statement",
      icon: <PieChart className="h-8 w-8" />,
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("reports:title")}
          subtitle={t("reports:subtitle")}
        />

        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {reports.map((report) => (
            <Link key={report.href} href={report.href}>
              <Card className="h-full hover:bg-muted/50 transition-colors cursor-pointer">
                <CardHeader>
                  <div className="flex items-center gap-4">
                    <div className="text-accent">{report.icon}</div>
                    <div>
                      <CardTitle>{report.title}</CardTitle>
                      <CardDescription>{report.description}</CardDescription>
                    </div>
                  </div>
                </CardHeader>
              </Card>
            </Link>
          ))}
        </div>
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
