import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/common";
import { AccountForm } from "@/components/forms/AccountForm";
import { useCreateAccount } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import type { AccountCreatePayload } from "@/types/account";

export default function NewAccountPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const createAccount = useCreateAccount();

  const handleSubmit = async (data: AccountCreatePayload) => {
    try {
      await createAccount.mutateAsync(data);
      toast({
        title: t("messages.success"),
        description: t("messages.saved"),
        variant: "success",
      });
      router.push("/accounting/chart-of-accounts");
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("accounting:chartOfAccounts.createAccount")}
          subtitle={t("accounting:chartOfAccounts.subtitle")}
        />

        <Card className="max-w-2xl">
          <CardHeader>
            <CardTitle>{t("accounting:chartOfAccounts.accountDetails")}</CardTitle>
          </CardHeader>
          <CardContent>
            <AccountForm
              onSubmit={handleSubmit}
              isSubmitting={createAccount.isPending}
              onCancel={() => router.back()}
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
