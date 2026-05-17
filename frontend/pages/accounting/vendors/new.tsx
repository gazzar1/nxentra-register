import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";
import { VendorForm } from "@/components/forms/VendorForm";
import { useCreateVendor } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import type { VendorCreatePayload } from "@/types/account";

export default function NewVendorPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const createVendor = useCreateVendor();

  const handleSubmit = async (data: Record<string, unknown>) => {
    try {
      await createVendor.mutateAsync(data as unknown as VendorCreatePayload);
      toast({
        title: "Vendor created",
        description: `Vendor ${data.name} has been created successfully.`,
      });
      router.push("/accounting/vendors");
    } catch (error: any) {
      const body = error?.response?.data;
      let description = "Failed to create vendor.";
      if (body?.detail) {
        description = body.detail;
      } else if (body && typeof body === "object") {
        const parts = Object.entries(body).map(([field, msgs]) => {
          const text = Array.isArray(msgs) ? msgs.join("; ") : String(msgs);
          return `${field}: ${text}`;
        });
        if (parts.length) description = parts.join(" | ");
      }
      toast({
        title: "Error",
        description,
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="New Vendor"
          subtitle="Add a new vendor to your accounts payable"
        />

        <Card>
          <CardHeader>
            <CardTitle>Vendor Information</CardTitle>
          </CardHeader>
          <CardContent>
            <VendorForm
              onSubmit={handleSubmit}
              isSubmitting={createVendor.isPending}
              onCancel={() => router.push("/accounting/vendors")}
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
