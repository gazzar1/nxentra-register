// pages/inventory/adjustments/new.tsx
// Create inventory adjustment page

import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { GetServerSideProps } from "next";

import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InventoryAdjustmentForm } from "@/components/forms/InventoryAdjustmentForm";
import { useCreateAdjustment } from "@/queries/useInventory";
import { InventoryAdjustmentPayload } from "@/types/inventory";
import { useToast } from "@/components/ui/toaster";

export default function NewAdjustmentPage() {
  const { t } = useTranslation(["common", "inventory"]);
  const router = useRouter();
  const createAdjustment = useCreateAdjustment();
  const { toast } = useToast();

  const handleSubmit = async (data: InventoryAdjustmentPayload) => {
    try {
      const result = await createAdjustment.mutateAsync(data);
      toast({
        title: t("inventory:adjustment.createSuccess", {
          count: result.data?.entry_count || data.lines.length,
        }),
        variant: "success",
      });
      router.push("/inventory/ledger");
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast({ title: err.response?.data?.error || t("common:error"), variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <PageHeader
        title={t("inventory:adjustment.create")}
        subtitle={t("inventory:adjustment.createSubtitle")}
        backHref="/inventory/balances"
      />

      <Card className="max-w-4xl">
        <CardHeader>
          <CardTitle>{t("inventory:adjustment.details")}</CardTitle>
        </CardHeader>
        <CardContent>
          <InventoryAdjustmentForm
            onSubmit={handleSubmit}
            isSubmitting={createAdjustment.isPending}
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
