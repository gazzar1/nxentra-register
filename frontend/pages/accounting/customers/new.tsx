import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";
import { CustomerForm } from "@/components/forms/CustomerForm";
import { useCreateCustomer } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import type { CustomerCreatePayload } from "@/types/account";

export default function NewCustomerPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const createCustomer = useCreateCustomer();

  const handleSubmit = async (data: Record<string, unknown>) => {
    try {
      await createCustomer.mutateAsync(data as unknown as CustomerCreatePayload);
      toast({
        title: "Customer created",
        description: `Customer ${data.name} has been created successfully.`,
      });
      router.push("/accounting/customers");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to create customer.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="New Customer"
          subtitle="Add a new customer to your accounts receivable"
        />

        <Card>
          <CardHeader>
            <CardTitle>Customer Information</CardTitle>
          </CardHeader>
          <CardContent>
            <CustomerForm
              onSubmit={handleSubmit}
              isSubmitting={createCustomer.isPending}
              onCancel={() => router.push("/accounting/customers")}
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
