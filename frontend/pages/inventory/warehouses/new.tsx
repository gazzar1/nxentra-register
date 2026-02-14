// pages/inventory/warehouses/new.tsx
// Create new warehouse page

import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { GetServerSideProps } from "next";

import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { WarehouseForm } from "@/components/forms/WarehouseForm";
import { useCreateWarehouse } from "@/queries/useInventory";
import { WarehouseCreatePayload } from "@/types/inventory";
import { useToast } from "@/components/ui/toaster";

export default function NewWarehousePage() {
  const { t } = useTranslation(["common", "inventory"]);
  const router = useRouter();
  const createWarehouse = useCreateWarehouse();
  const { toast } = useToast();

  const handleSubmit = async (data: WarehouseCreatePayload) => {
    try {
      await createWarehouse.mutateAsync(data);
      toast({ title: t("inventory:warehouses.createSuccess"), variant: "success" });
      router.push("/inventory/warehouses");
    } catch (error: unknown) {
      console.error("Create warehouse error:", error);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const err = error as any;
      const responseData = err?.response?.data;
      console.error("Response data:", responseData);
      const errorMessage = responseData?.error || responseData?.detail || responseData?.message || err?.message || t("common:error");
      toast({ title: String(errorMessage), variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <PageHeader
        title={t("inventory:warehouses.create")}
        subtitle={t("inventory:warehouses.createSubtitle")}
        backHref="/inventory/warehouses"
      />

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>{t("inventory:warehouses.details")}</CardTitle>
        </CardHeader>
        <CardContent>
          <WarehouseForm
            onSubmit={handleSubmit}
            isSubmitting={createWarehouse.isPending}
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
