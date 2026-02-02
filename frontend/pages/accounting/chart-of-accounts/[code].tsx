import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { AccountForm } from "@/components/forms/AccountForm";
import { useAccount, useUpdateAccount, useDeleteAccount, useDimensions } from "@/queries/useAccounts";
import { accountsService } from "@/services/accounts.service";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import type { AccountCreatePayload, AccountAnalysisDefault } from "@/types/account";
import { Badge } from "@/components/ui/badge";
import { Trash2, BookOpen } from "lucide-react";

export default function EditAccountPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { code } = router.query;
  const accountCode = typeof code === "string" ? code : "";

  const { data: account, isLoading } = useAccount(accountCode);
  const { data: dimensions } = useDimensions();
  const { toast } = useToast();
  const updateAccount = useUpdateAccount();
  const deleteAccount = useDeleteAccount();

  // Analysis dimensions state
  const [accountDefaults, setAccountDefaults] = useState<AccountAnalysisDefault[]>([]);
  const [dimSelections, setDimSelections] = useState<Record<number, string>>({});
  const [dimChecks, setDimChecks] = useState<Record<number, boolean>>({});
  const [loadingDefaults, setLoadingDefaults] = useState(false);

  // Fetch account analysis defaults when account code changes
  useEffect(() => {
    if (!accountCode || !dimensions?.length) return;

    const fetchDefaults = async () => {
      setLoadingDefaults(true);
      try {
        const { data } = await accountsService.getAnalysisDefaults(accountCode);
        setAccountDefaults(data);

        const selections: Record<number, string> = {};
        const checks: Record<number, boolean> = {};
        data.forEach((def) => {
          const dim = dimensions.find((d) => d.id === def.dimension);
          if (dim) {
            if (dim.applies_to_account_types.length > 0) {
              selections[dim.id] = def.default_value.toString();
            } else {
              checks[dim.id] = true;
            }
          }
        });
        setDimSelections(selections);
        setDimChecks(checks);
      } catch {
        setAccountDefaults([]);
        setDimSelections({});
        setDimChecks({});
      } finally {
        setLoadingDefaults(false);
      }
    };

    fetchDefaults();
  }, [accountCode, dimensions]);

  const handleSubmit = async (data: AccountCreatePayload) => {
    try {
      await updateAccount.mutateAsync({
        code: accountCode,
        data: {
          name: data.name,
          name_ar: data.name_ar,
          description: data.description,
          description_ar: data.description_ar,
        },
      });

      // Save dimension selections
      if (dimensions) {
        for (const dim of dimensions) {
          const isCoAType = dim.applies_to_account_types.length > 0;
          const currentDefault = accountDefaults.find((d) => d.dimension === dim.id);

          if (isCoAType) {
            const selectedValueId = dimSelections[dim.id];
            if (selectedValueId) {
              await accountsService.setAnalysisDefault(accountCode, dim.id, parseInt(selectedValueId));
            } else if (currentDefault) {
              await accountsService.removeAnalysisDefault(accountCode, dim.id);
            }
          } else {
            const isChecked = dimChecks[dim.id];
            if (isChecked && !currentDefault) {
              const firstValue = dim.values?.[0];
              if (firstValue) {
                await accountsService.setAnalysisDefault(accountCode, dim.id, firstValue.id);
              }
            } else if (!isChecked && currentDefault) {
              await accountsService.removeAnalysisDefault(accountCode, dim.id);
            }
          }
        }
      }

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

  const handleDelete = async () => {
    if (!confirm(t("accounting:chartOfAccounts.deleteConfirm", "Are you sure you want to delete this account?"))) {
      return;
    }
    try {
      await deleteAccount.mutateAsync(accountCode);
      toast({
        title: t("messages.success"),
        description: t("accounting:chartOfAccounts.deleteAccount"),
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

  if (isLoading) {
    return (
      <AppLayout>
        <div className="flex justify-center py-12">
          <LoadingSpinner size="lg" />
        </div>
      </AppLayout>
    );
  }

  if (!account) {
    return (
      <AppLayout>
        <div className="flex flex-col items-center justify-center py-12 space-y-4">
          <p className="text-muted-foreground">{t("messages.notFound", "Account not found")}</p>
          <Button variant="outline" onClick={() => router.push("/accounting/chart-of-accounts")}>
            {t("actions.back")}
          </Button>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("accounting:chartOfAccounts.editAccount")}
          subtitle={`${account.code} - ${account.name}`}
          actions={
            <div className="flex items-center gap-3">
              {account.has_transactions && (
                <Badge variant="secondary" className="gap-1">
                  <BookOpen className="h-3 w-3" />
                  {t("accounting:chartOfAccounts.hasTransactions", "Transactions Present")}
                </Badge>
              )}
              <Button
                variant="destructive"
                onClick={handleDelete}
                disabled={deleteAccount.isPending || account.has_transactions}
                title={account.has_transactions ? t("accounting:chartOfAccounts.cannotDeleteWithTransactions", "Cannot delete account with transactions") : undefined}
              >
                <Trash2 className="me-2 h-4 w-4" />
                {t("accounting:chartOfAccounts.deleteAccount")}
              </Button>
            </div>
          }
        />

        <Card className="max-w-2xl">
          <CardHeader>
            <CardTitle>{t("accounting:chartOfAccounts.accountDetails")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <AccountForm
              initialData={account}
              onSubmit={handleSubmit}
              isSubmitting={updateAccount.isPending}
              onCancel={() => router.back()}
              extraContent={
                dimensions && dimensions.length > 0 ? (
                  <div className="border-t pt-6 mt-6 space-y-4">
                    <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                      {t("accounting:dimensions.title", "Analysis Dimensions")}
                    </h3>
                    {dimensions.map((dim) => {
                      const isCoAType = dim.applies_to_account_types.length > 0;

                      if (isCoAType) {
                        return (
                          <div key={dim.id} className="space-y-2">
                            <Label>{dim.name}</Label>
                            <Select
                              value={dimSelections[dim.id] || "__none__"}
                              onValueChange={(value) =>
                                setDimSelections({
                                  ...dimSelections,
                                  [dim.id]: value === "__none__" ? "" : value,
                                })
                              }
                            >
                              <SelectTrigger>
                                <SelectValue placeholder={`-- ${t("actions.select", "Select")} --`} />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="__none__">-- {t("actions.select", "Select")} --</SelectItem>
                                {dim.values?.map((val) => (
                                  <SelectItem key={val.id} value={val.id.toString()}>
                                    {val.code} - {val.name}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        );
                      } else {
                        return (
                          <div key={dim.id} className="flex items-center gap-2">
                            <input
                              type="checkbox"
                              id={`dim-${dim.id}`}
                              checked={dimChecks[dim.id] || false}
                              onChange={(e) =>
                                setDimChecks({ ...dimChecks, [dim.id]: e.target.checked })
                              }
                              className="h-4 w-4 rounded border-input"
                            />
                            <Label htmlFor={`dim-${dim.id}`} className="font-normal">
                              {dim.name}
                            </Label>
                          </div>
                        );
                      }
                    })}
                  </div>
                ) : undefined
              }
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
