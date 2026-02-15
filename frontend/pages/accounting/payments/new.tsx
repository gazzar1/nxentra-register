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
import { useVendors, useAccounts } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import { vendorPaymentsService, type VendorPaymentCreatePayload } from "@/services/accounts.service";

interface PaymentFormData {
  vendor_id: string;
  payment_date: string;
  amount: string;
  bank_account_id: string;
  ap_control_account_id: string;
  reference: string;
  memo: string;
}

export default function NewVendorPaymentPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: vendors } = useVendors();
  const { data: accounts } = useAccounts();

  // Filter accounts by role
  const bankAccounts = accounts?.filter(
    (a) => a.role === "CASH" && a.is_postable && !a.is_header
  );
  const apControlAccounts = accounts?.filter(
    (a) => a.role === "PAYABLE_CONTROL" && a.is_postable && !a.is_header
  );

  const {
    register,
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<PaymentFormData>({
    defaultValues: {
      vendor_id: "",
      payment_date: new Date().toISOString().split("T")[0],
      amount: "",
      bank_account_id: "",
      ap_control_account_id: "",
      reference: "",
      memo: "",
    },
  });

  const onSubmit = async (data: PaymentFormData) => {
    try {
      const payload: VendorPaymentCreatePayload = {
        vendor_id: parseInt(data.vendor_id),
        payment_date: data.payment_date,
        amount: data.amount,
        bank_account_id: parseInt(data.bank_account_id),
        ap_control_account_id: parseInt(data.ap_control_account_id),
        reference: data.reference,
        memo: data.memo,
      };

      await vendorPaymentsService.create(payload);
      toast({
        title: "Payment recorded",
        description: `Vendor payment has been recorded successfully.`,
      });
      router.push("/accounting/payments");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to record payment.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <PageHeader
          title={t("accounting:newPayment", "New Vendor Payment")}
          subtitle={t("accounting:newPaymentSubtitle", "Record a payment made to a vendor")}
          actions={
            <div className="flex gap-2">
              <Link href="/accounting/payments">
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
            <CardTitle>{t("accounting:paymentDetails", "Payment Details")}</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="vendor_id">{t("accounting:vendor", "Vendor")} *</Label>
              <Controller
                name="vendor_id"
                control={control}
                rules={{ required: t("accounting:vendorRequired", "Vendor is required") }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder={t("accounting:selectVendor", "Select vendor")} />
                    </SelectTrigger>
                    <SelectContent>
                      {vendors?.map((vendor) => (
                        <SelectItem key={vendor.id} value={vendor.id.toString()}>
                          {vendor.code} - {vendor.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.vendor_id && (
                <p className="text-sm text-destructive">{errors.vendor_id.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="payment_date">{t("accounting:paymentDate", "Payment Date")} *</Label>
              <Input
                id="payment_date"
                type="date"
                {...register("payment_date", { required: t("accounting:dateRequired", "Date is required") })}
              />
              {errors.payment_date && (
                <p className="text-sm text-destructive">{errors.payment_date.message}</p>
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
              <Label htmlFor="ap_control_account_id">{t("accounting:apControlAccount", "AP Control Account")} *</Label>
              <Controller
                name="ap_control_account_id"
                control={control}
                rules={{ required: t("accounting:apControlRequired", "AP control account is required") }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder={t("accounting:selectApControl", "Select AP control")} />
                    </SelectTrigger>
                    <SelectContent>
                      {apControlAccounts?.map((account) => (
                        <SelectItem key={account.id} value={account.id.toString()}>
                          {account.code} - {account.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.ap_control_account_id && (
                <p className="text-sm text-destructive">{errors.ap_control_account_id.message}</p>
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
              <p>{t("accounting:paymentJournalExplanation", "This payment will create the following journal entry:")}</p>
              <ul className="mt-2 list-disc list-inside space-y-1">
                <li>{t("accounting:debitAp", "Debit: AP Control Account (reduces payable)")}</li>
                <li>{t("accounting:creditBank", "Credit: Bank Account (reduces cash)")}</li>
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
