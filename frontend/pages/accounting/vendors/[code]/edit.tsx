import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { VendorForm } from "@/components/forms/VendorForm";
import { RecordNavigator } from "@/components/forms/RecordNavigator";
import { useVendor, useUpdateVendor, useVendors } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import { useUnsavedChangesGuard } from "@/lib/useUnsavedChangesGuard";
import type { VendorUpdatePayload } from "@/types/account";

export default function EditVendorPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const code = router.query.code as string;
  const { data: vendor, isLoading } = useVendor(code);
  const { data: allVendors } = useVendors();
  const updateVendor = useUpdateVendor();
  const [isDirty, setIsDirty] = useState(false);
  useUnsavedChangesGuard(isDirty);

  const handleSubmit = async (data: Record<string, unknown>) => {
    try {
      await updateVendor.mutateAsync({ code, data: data as unknown as VendorUpdatePayload });
      toast({
        title: "Vendor updated",
        description: `${data.name || vendor?.name} has been updated successfully.`,
      });
      router.push(`/accounting/vendors/${code}`);
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to update vendor.",
        variant: "destructive",
      });
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <LoadingSpinner />
      </AppLayout>
    );
  }

  if (!vendor) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <h2 className="text-lg font-semibold">Vendor not found</h2>
          <p className="text-muted-foreground mt-2">The vendor you&apos;re looking for doesn&apos;t exist.</p>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={`Edit ${vendor.name}`}
          subtitle={`Update vendor information for ${vendor.code}`}
          actions={
            <RecordNavigator
              records={allVendors}
              currentKey={code}
              getKey={(v) => v.code}
              getLabel={(v) => `${v.code} - ${v.name}`}
              basePath="/accounting/vendors"
            />
          }
        />

        <Card>
          <CardHeader>
            <CardTitle>Vendor Information</CardTitle>
          </CardHeader>
          <CardContent>
            <VendorForm
              initialData={vendor}
              onSubmit={handleSubmit}
              isSubmitting={updateVendor.isPending}
              onCancel={() => router.push(`/accounting/vendors/${code}`)}
              isEdit
              onDirtyChange={setIsDirty}
            />
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])),
    },
  };
};
