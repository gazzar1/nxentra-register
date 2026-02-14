// pages/inventory/warehouses/[id]/edit.tsx
// Edit warehouse page

import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { GetServerSideProps } from "next";

import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { WarehouseForm } from "@/components/forms/WarehouseForm";
import { useWarehouse, useUpdateWarehouse } from "@/queries/useInventory";
import { WarehouseUpdatePayload } from "@/types/inventory";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toaster";

export default function EditWarehousePage() {
  const { t } = useTranslation(["common", "inventory"]);
  const router = useRouter();
  const { id } = router.query;
  const warehouseId = Number(id);
  const { toast } = useToast();

  const { data: warehouse, isLoading } = useWarehouse(warehouseId);
  const updateWarehouse = useUpdateWarehouse();

  const handleSubmit = async (data: WarehouseUpdatePayload) => {
    try {
      await updateWarehouse.mutateAsync({ id: warehouseId, data });
      toast({ title: t("inventory:warehouses.updateSuccess"), variant: "success" });
      router.push("/inventory/warehouses");
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast({ title: err.response?.data?.error || t("common:error"), variant: "destructive" });
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <PageHeader
          title={t("inventory:warehouses.edit")}
          backHref="/inventory/warehouses"
        />
        <Card className="max-w-2xl">
          <CardHeader>
            <Skeleton className="h-6 w-48" />
          </CardHeader>
          <CardContent className="space-y-4">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </CardContent>
        </Card>
      </AppLayout>
    );
  }

  if (!warehouse) {
    return (
      <AppLayout>
        <PageHeader
          title={t("inventory:warehouses.edit")}
          backHref="/inventory/warehouses"
        />
        <Card className="max-w-2xl">
          <CardContent className="py-8 text-center text-muted-foreground">
            {t("inventory:warehouses.notFound")}
          </CardContent>
        </Card>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <PageHeader
        title={t("inventory:warehouses.edit")}
        subtitle={warehouse.name}
        backHref="/inventory/warehouses"
      />

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>{t("inventory:warehouses.details")}</CardTitle>
        </CardHeader>
        <CardContent>
          <WarehouseForm
            initialData={warehouse}
            onSubmit={handleSubmit}
            isSubmitting={updateWarehouse.isPending}
            isEdit
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
