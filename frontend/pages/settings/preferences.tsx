import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/common";

export default function PreferencesPage() {
  const { t } = useTranslation(["common", "settings"]);
  const router = useRouter();
  const { locale } = router;

  const changeLanguage = (newLocale: string) => {
    router.push(router.pathname, router.asPath, { locale: newLocale });
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("settings:preferences.title")}
          subtitle={t("settings:preferences.subtitle")}
        />

        <Card className="max-w-2xl">
          <CardHeader>
            <CardTitle>{t("settings:preferences.language")}</CardTitle>
            <CardDescription>
              {t("language.switchTo")} {locale === "en" ? t("language.ar") : t("language.en")}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>{t("settings:preferences.language")}</Label>
                <Select value={locale} onValueChange={changeLanguage}>
                  <SelectTrigger className="w-full max-w-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="en">
                      <span className="me-2">🇺🇸</span>
                      {t("language.en")}
                    </SelectItem>
                    <SelectItem value="ar">
                      <span className="me-2">🇸🇦</span>
                      {t("language.ar")}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>
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
