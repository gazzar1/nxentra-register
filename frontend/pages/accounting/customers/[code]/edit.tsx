import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { CustomerForm } from "@/components/forms/CustomerForm";
import { useCustomer, useUpdateCustomer } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import type { CustomerUpdatePayload } from "@/types/account";

export default function EditCustomerPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const code = router.query.code as string;
  const { data: customer, isLoading } = useCustomer(code);
  const updateCustomer = useUpdateCustomer();

  const handleSubmit = async (data: Record<string, unknown>) => {
    try {
      await updateCustomer.mutateAsync({ code, data: data as unknown as CustomerUpdatePayload });
      toast({
        title: "Customer updated",
        description: `${data.name || customer?.name} has been updated successfully.`,
      });
      router.push(`/accounting/customers/${code}`);
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to update customer.",
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

  if (!customer) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <h2 className="text-lg font-semibold">Customer not found</h2>
          <p className="text-muted-foreground mt-2">The customer you're looking for doesn't exist.</p>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={`Edit ${customer.name}`}
          subtitle={`Update customer information for ${customer.code}`}
        />

        <Card>
          <CardHeader>
            <CardTitle>Customer Information</CardTitle>
          </CardHeader>
          <CardContent>
            <CustomerForm
              initialData={customer}
              onSubmit={handleSubmit}
              isSubmitting={updateCustomer.isPending}
              onCancel={() => router.push(`/accounting/customers/${code}`)}
              isEdit
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
