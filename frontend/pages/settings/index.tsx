import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { Building2, User, Globe, ShieldCheck } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";

export default function SettingsIndexPage() {
  const { t } = useTranslation(["common", "settings"]);

  const settingsSections = [
    {
      title: t("settings:company.title"),
      description: t("settings:company.subtitle"),
      href: "/settings/company",
      icon: <Building2 className="h-8 w-8" />,
    },
    {
      title: t("settings:preferences.title"),
      description: t("settings:preferences.subtitle"),
      href: "/settings/preferences",
      icon: <User className="h-8 w-8" />,
    },
    {
      title: t("settings:audit.title", "Event Audit"),
      description: t("settings:audit.subtitle", "Monitor event stream integrity and audit trail"),
      href: "/settings/audit",
      icon: <ShieldCheck className="h-8 w-8" />,
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("settings:title")}
          subtitle={t("settings:subtitle")}
        />

        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {settingsSections.map((section) => (
            <Link key={section.href} href={section.href}>
              <Card className="h-full hover:bg-muted/50 transition-colors cursor-pointer">
                <CardHeader>
                  <div className="flex items-center gap-4">
                    <div className="text-accent">{section.icon}</div>
                    <div>
                      <CardTitle>{section.title}</CardTitle>
                      <CardDescription>{section.description}</CardDescription>
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
      ...(await serverSideTranslations(locale ?? "en", ["common", "settings"])),
    },
  };
};
