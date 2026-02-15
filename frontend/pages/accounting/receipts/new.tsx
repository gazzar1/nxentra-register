import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Save } from "lucide-react";
import { useForm, Controller } from "react-hook-form";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/common";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCustomers, useAccounts } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import { customerReceiptsService, type CustomerReceiptCreatePayload } from "@/services/accounts.service";

interface ReceiptFormData {
  customer_id: string;
  receipt_date: string;
  amount: string;
  bank_account_id: string;
  ar_control_account_id: string;
  reference: string;
  memo: string;
}

export default function NewCustomerReceiptPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: customers } = useCustomers();
  const { data: accounts } = useAccounts();

  // Filter accounts by role
  const bankAccounts = accounts?.filter(
    (a) => a.role === "CASH" && a.is_postable && !a.is_header
  );
  const arControlAccounts = accounts?.filter(
    (a) => a.role === "RECEIVABLE_CONTROL" && a.is_postable && !a.is_header
  );

  const {
    register,
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ReceiptFormData>({
    defaultValues: {
      customer_id: "",
      receipt_date: new Date().toISOString().split("T")[0],
      amount: "",
      bank_account_id: "",
      ar_control_account_id: "",
      reference: "",
      memo: "",
    },
  });

  const onSubmit = async (data: ReceiptFormData) => {
    try {
      const payload: CustomerReceiptCreatePayload = {
        customer_id: parseInt(data.customer_id),
        receipt_date: data.receipt_date,
        amount: data.amount,
        bank_account_id: parseInt(data.bank_account_id),
        ar_control_account_id: parseInt(data.ar_control_account_id),
        reference: data.reference,
        memo: data.memo,
      };

      await customerReceiptsService.create(payload);
      toast({
        title: "Receipt recorded",
        description: `Customer receipt has been recorded successfully.`,
      });
      router.push("/accounting/receipts");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to record receipt.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <PageHeader
          title={t("accounting:newReceipt", "New Customer Receipt")}
          subtitle={t("accounting:newReceiptSubtitle", "Record a payment received from a customer")}
          actions={
            <div className="flex gap-2">
              <Link href="/accounting/receipts">
                <Button type="button" variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />
                  {t("common:cancel")}
                </Button>
              </Link>
              <Button type="submit" disabled={isSubmitting}>
                <Save className="h-4 w-4 me-2" />
                {isSubmitting ? t("common:saving") : t("common:save")}
              </Button>
            </div>
          }
        />

        <Card>
          <CardHeader>
            <CardTitle>{t("accounting:receiptDetails", "Receipt Details")}</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="customer_id">{t("accounting:customer", "Customer")} *</Label>
              <Controller
                name="customer_id"
                control={control}
                rules={{ required: t("accounting:customerRequired", "Customer is required") }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder={t("accounting:selectCustomer", "Select customer")} />
                    </SelectTrigger>
                    <SelectContent>
                      {customers?.map((customer) => (
                        <SelectItem key={customer.id} value={customer.id.toString()}>
                          {customer.code} - {customer.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.customer_id && (
                <p className="text-sm text-destructive">{errors.customer_id.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="receipt_date">{t("accounting:receiptDate", "Receipt Date")} *</Label>
              <Input
                id="receipt_date"
                type="date"
                {...register("receipt_date", { required: t("accounting:dateRequired", "Date is required") })}
              />
              {errors.receipt_date && (
                <p className="text-sm text-destructive">{errors.receipt_date.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="amount">{t("accounting:amount", "Amount")} *</Label>
              <Input
                id="amount"
                type="number"
                step="0.01"
                min="0.01"
                {...register("amount", {
                  required: t("accounting:amountRequired", "Amount is required"),
                  validate: (v) => parseFloat(v) > 0 || t("accounting:amountPositive", "Amount must be positive"),
                })}
                placeholder="0.00"
              />
              {errors.amount && (
                <p className="text-sm text-destructive">{errors.amount.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="bank_account_id">{t("accounting:bankAccount", "Bank Account")} *</Label>
              <Controller
                name="bank_account_id"
                control={control}
                rules={{ required: t("accounting:bankAccountRequired", "Bank account is required") }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder={t("accounting:selectBankAccount", "Select bank account")} />
                    </SelectTrigger>
                    <SelectContent>
                      {bankAccounts?.map((account) => (
                        <SelectItem key={account.id} value={account.id.toString()}>
                          {account.code} - {account.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.bank_account_id && (
                <p className="text-sm text-destructive">{errors.bank_account_id.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="ar_control_account_id">{t("accounting:arControlAccount", "AR Control Account")} *</Label>
              <Controller
                name="ar_control_account_id"
                control={control}
                rules={{ required: t("accounting:arControlRequired", "AR control account is required") }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder={t("accounting:selectArControl", "Select AR control")} />
                    </SelectTrigger>
                    <SelectContent>
                      {arControlAccounts?.map((account) => (
                        <SelectItem key={account.id} value={account.id.toString()}>
                          {account.code} - {account.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.ar_control_account_id && (
                <p className="text-sm text-destructive">{errors.ar_control_account_id.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="reference">{t("accounting:reference", "Reference")}</Label>
              <Input
                id="reference"
                {...register("reference")}
                placeholder={t("accounting:referencePlaceholder", "Check number, transfer ID, etc.")}
              />
            </div>

            <div className="space-y-2 md:col-span-2 lg:col-span-3">
              <Label htmlFor="memo">{t("accounting:memo", "Memo")}</Label>
              <Textarea
                id="memo"
                {...register("memo")}
                placeholder={t("accounting:memoPlaceholder", "Additional notes...")}
                rows={2}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("accounting:journalEntryPreview", "Journal Entry Preview")}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-sm text-muted-foreground">
              <p>{t("accounting:receiptJournalExplanation", "This receipt will create the following journal entry:")}</p>
              <ul className="mt-2 list-disc list-inside space-y-1">
                <li>{t("accounting:debitBank", "Debit: Bank Account (increases cash)")}</li>
                <li>{t("accounting:creditAr", "Credit: AR Control Account (reduces receivable)")}</li>
              </ul>
            </div>
          </CardContent>
        </Card>
      </form>
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
