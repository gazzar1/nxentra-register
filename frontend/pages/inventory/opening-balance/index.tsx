// pages/inventory/opening-balance/index.tsx
// Record inventory opening balance page

import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { GetServerSideProps } from "next";

import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { OpeningBalanceForm } from "@/components/forms/OpeningBalanceForm";
import { useCreateOpeningBalance } from "@/queries/useInventory";
import { OpeningBalancePayload } from "@/types/inventory";
import { useToast } from "@/components/ui/toaster";

export default function OpeningBalancePage() {
  const { t } = useTranslation(["common", "inventory"]);
  const router = useRouter();
  const createOpeningBalance = useCreateOpeningBalance();
  const { toast } = useToast();

  const handleSubmit = async (data: OpeningBalancePayload) => {
    try {
      const result = await createOpeningBalance.mutateAsync(data);
      toast({
        title: t("inventory:openingBalance.createSuccess", {
          count: result.data?.entry_count || data.lines.length,
        }),
        variant: "success",
      });
      router.push("/inventory/balances");
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast({ title: err.response?.data?.error || t("common:error"), variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <PageHeader
        title={t("inventory:openingBalance.title")}
        subtitle={t("inventory:openingBalance.subtitle")}
        backHref="/inventory/balances"
      />

      <Card className="max-w-4xl">
        <CardHeader>
          <CardTitle>{t("inventory:openingBalance.details")}</CardTitle>
          <CardDescription>
            {t("inventory:openingBalance.description")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <OpeningBalanceForm
            onSubmit={handleSubmit}
            isSubmitting={createOpeningBalance.isPending}
          />
        </CardContent>
      </Card>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "inventory"])),
    },
  };
};
