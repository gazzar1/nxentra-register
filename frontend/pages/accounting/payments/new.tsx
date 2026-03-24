import { GetServerSideProps } from "next";
import { useState, useEffect, useMemo } from "react";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Save, FileText, Plus, Trash2 } from "lucide-react";
import { useForm, Controller, useFieldArray } from "react-hook-form";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useVendors, useAccounts } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import {
  vendorPaymentsService,
  type VendorPaymentCreatePayload,
  type PaymentAllocation,
} from "@/services/accounts.service";
import { purchaseBillsService } from "@/services/purchases.service";
import type { PurchaseBillListItem } from "@/types/purchases";
import { periodsService, type FiscalPeriod } from "@/services/periods.service";
import { exchangeRatesService } from "@/services/exchange-rates.service";
import { useCompanySettings } from "@/queries/useCompanySettings";
import { cn } from "@/lib/cn";

interface BillAllocationFormData {
  bill_reference: string;
  amount: string;
  bill_date?: string;
  bill_amount?: string;
}

interface PaymentFormData {
  vendor_id: string;
  payment_date: string;
  accounting_date: string;
  amount: string;
  bank_account_id: string;
  ap_control_account_id: string;
  reference: string;
  memo: string;
  allocations: BillAllocationFormData[];
}

export default function NewVendorPaymentPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: vendors } = useVendors();
  const { data: accounts } = useAccounts();

  // Filter accounts by role
  const bankAccounts = accounts?.filter(
    (a) => a.role === "LIQUIDITY" && a.is_postable && !a.is_header
  );
  const apControlAccounts = accounts?.filter(
    (a) => a.role === "PAYABLE_CONTROL" && a.is_postable && !a.is_header
  );

  const {
    register,
    control,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<PaymentFormData>({
    defaultValues: {
      vendor_id: "",
      payment_date: new Date().toISOString().split("T")[0],
      accounting_date: new Date().toISOString().split("T")[0],
      amount: "",
      bank_account_id: "",
      ap_control_account_id: "",
      reference: "",
      memo: "",
      allocations: [],
    },
  });

  const { fields, append, remove } = useFieldArray({
    control,
    name: "allocations",
  });

  const paymentAmount = watch("amount");
  const allocations = watch("allocations");
  const watchPaymentDate = watch("payment_date");
  const watchAccountingDate = watch("accounting_date");

  const [vendorBills, setVendorBills] = useState<PurchaseBillListItem[]>([]);
  const [periods, setPeriods] = useState<FiscalPeriod[]>([]);
  const [resolvedPeriod, setResolvedPeriod] = useState<string>("");
  const [paymentCurrency, setPaymentCurrency] = useState<string>("");
  const [exchangeRate, setExchangeRate] = useState<string>("1");
  const [availableCurrencies, setAvailableCurrencies] = useState<string[]>([]);
  const { data: companySettings } = useCompanySettings();
  const functionalCurrency = companySettings?.functional_currency || companySettings?.default_currency || "USD";
  const selectedVendorId = watch("vendor_id");

  // Fetch fiscal periods and available currencies on mount
  useEffect(() => {
    periodsService.list().then((res) => {
      setPeriods(res.data.periods || []);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!functionalCurrency) return;
    exchangeRatesService.list().then((res) => {
      const codes = new Set<string>();
      codes.add(functionalCurrency);
      res.data.forEach((r) => { codes.add(r.from_currency); codes.add(r.to_currency); });
      setAvailableCurrencies(Array.from(codes).sort());
    }).catch(() => {});
  }, [functionalCurrency]);

  // Auto-set currency from vendor and fetch open bills
  useEffect(() => {
    if (selectedVendorId && vendors) {
      const vend = vendors.find((v) => String(v.id) === selectedVendorId);
      if (vend?.currency) {
        setPaymentCurrency(vend.currency);
      } else {
        setPaymentCurrency(functionalCurrency);
      }
      // Fetch posted (open) bills for this vendor
      purchaseBillsService.list({ vendor_id: parseInt(selectedVendorId), status: "POSTED" })
        .then((res) => setVendorBills(res.data))
        .catch(() => setVendorBills([]));
    } else {
      setVendorBills([]);
    }
  }, [selectedVendorId, vendors, functionalCurrency]);

  // Auto-lookup exchange rate when currency or date changes
  useEffect(() => {
    if (!paymentCurrency || paymentCurrency === functionalCurrency) {
      setExchangeRate("1");
      return;
    }
    const dateStr = watchPaymentDate || new Date().toISOString().split("T")[0];
    exchangeRatesService
      .lookup({ from_currency: paymentCurrency, to_currency: functionalCurrency, date: dateStr })
      .then((res) => {
        if (res.data.rate) {
          setExchangeRate(res.data.rate);
        }
      })
      .catch(() => {});
  }, [paymentCurrency, watchPaymentDate, functionalCurrency]);

  // Sync accounting_date with payment_date
  useEffect(() => {
    if (watchPaymentDate) {
      setValue("accounting_date", watchPaymentDate);
    }
  }, [watchPaymentDate, setValue]);

  // Resolve period from accounting_date
  useEffect(() => {
    if (!watchAccountingDate || periods.length === 0) {
      setResolvedPeriod("");
      return;
    }
    const d = new Date(watchAccountingDate);
    const match = periods.find((p) => {
      const start = new Date(p.start_date);
      const end = new Date(p.end_date);
      return d >= start && d <= end && p.period_type === "NORMAL";
    });
    if (match) {
      setResolvedPeriod(`Period ${match.period} (${match.start_date} — ${match.end_date})${match.status === "OPEN" ? "" : " ⚠ CLOSED"}`);
    } else {
      setResolvedPeriod("No matching open period");
    }
  }, [watchAccountingDate, periods]);

  // Calculate total allocated amount
  const totalAllocated = useMemo(() => {
    return allocations
      .filter((a) => parseFloat(a.amount || "0") > 0)
      .reduce((sum, a) => sum + parseFloat(a.amount || "0"), 0);
  }, [allocations]);

  // Calculate unallocated amount
  const unallocatedAmount = useMemo(() => {
    const payment = parseFloat(paymentAmount || "0");
    return Math.max(0, payment - totalAllocated);
  }, [paymentAmount, totalAllocated]);

  const handleAddAllocation = () => {
    append({
      bill_reference: "",
      amount: unallocatedAmount > 0 ? unallocatedAmount.toFixed(2) : "",
      bill_date: "",
      bill_amount: "",
    });
  };

  const onSubmit = async (data: PaymentFormData) => {
    // Build allocations array
    const validAllocations: PaymentAllocation[] = data.allocations
      .filter((a) => a.bill_reference && parseFloat(a.amount) > 0)
      .map((a) => ({
        bill_reference: a.bill_reference,
        amount: a.amount,
        bill_date: a.bill_date || undefined,
        bill_amount: a.bill_amount || undefined,
      }));

    // Validate total allocated doesn't exceed payment amount
    if (totalAllocated > parseFloat(data.amount)) {
      toast({
        title: t("accounting:allocationError", "Allocation Error"),
        description: t("accounting:allocationExceedsPayment", "Total allocated amount exceeds payment amount."),
        variant: "destructive",
      });
      return;
    }

    try {
      const payload: VendorPaymentCreatePayload = {
        vendor_id: parseInt(data.vendor_id),
        payment_date: data.payment_date,
        accounting_date: data.accounting_date,
        amount: data.amount,
        bank_account_id: parseInt(data.bank_account_id),
        ap_control_account_id: parseInt(data.ap_control_account_id),
        reference: data.reference,
        memo: data.memo,
        allocations: validAllocations.length > 0 ? validAllocations : undefined,
        currency: paymentCurrency || functionalCurrency,
        exchange_rate: exchangeRate,
      };

      await vendorPaymentsService.create(payload);
      toast({
        title: t("accounting:paymentRecorded", "Payment recorded"),
        description: validAllocations.length > 0
          ? t("accounting:paymentWithAllocations", `Payment recorded and applied to ${validAllocations.length} bill(s).`)
          : t("accounting:paymentRecordedSuccess", "Vendor payment has been recorded successfully."),
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

  const formatCurrency = (amount: string | number) => {
    const num = typeof amount === "string" ? parseFloat(amount) : amount;
    return new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(num);
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
              <Label htmlFor="accounting_date">{t("accounting:accountingDate", "Accounting Date")} *</Label>
              <Input
                id="accounting_date"
                type="date"
                {...register("accounting_date", { required: t("accounting:dateRequired", "Date is required") })}
              />
              {resolvedPeriod && (
                <p className={cn(
                  "text-xs",
                  resolvedPeriod.includes("CLOSED") || resolvedPeriod.includes("No matching")
                    ? "text-destructive"
                    : "text-muted-foreground"
                )}>
                  {resolvedPeriod}
                </p>
              )}
              {errors.accounting_date && (
                <p className="text-sm text-destructive">{errors.accounting_date.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label>{t("accounting:currency", "Currency")}</Label>
              <Select value={paymentCurrency} onValueChange={setPaymentCurrency}>
                <SelectTrigger>
                  <SelectValue placeholder={functionalCurrency} />
                </SelectTrigger>
                <SelectContent>
                  {availableCurrencies.map((code) => (
                    <SelectItem key={code} value={code}>
                      {code}{code === functionalCurrency ? " (functional)" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
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
              <Label>{t("accounting:exchangeRate", "Exchange Rate")}</Label>
              <Input
                type="number"
                step="0.000001"
                value={exchangeRate}
                onChange={(e) => setExchangeRate(e.target.value)}
                disabled={paymentCurrency === functionalCurrency || !paymentCurrency}
              />
              {paymentCurrency && paymentCurrency !== functionalCurrency && (
                <p className="text-xs text-muted-foreground">
                  1 {paymentCurrency} = {exchangeRate} {functionalCurrency}
                </p>
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

        {/* Bill Allocation Section */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <FileText className="h-5 w-5" />
                  {t("accounting:billAllocation", "Bill Allocation")}
                </CardTitle>
                <CardDescription>
                  {t("accounting:billAllocationDescription", "Optionally specify which vendor bills this payment applies to")}
                </CardDescription>
              </div>
              <Button type="button" variant="outline" size="sm" onClick={handleAddAllocation}>
                <Plus className="h-4 w-4 me-2" />
                {t("accounting:addBill", "Add Bill")}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {fields.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">
                {t("accounting:noBillAllocations", "No bill allocations added. Click 'Add Bill' to specify which bills this payment applies to.")}
              </p>
            ) : (
              <>
                <div className="rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t("accounting:billReference", "Bill Reference")} *</TableHead>
                        <TableHead>{t("accounting:billDate", "Bill Date")}</TableHead>
                        <TableHead className="text-right">{t("accounting:billAmount", "Bill Amount")}</TableHead>
                        <TableHead className="text-right">{t("accounting:payAmount", "Pay Amount")} *</TableHead>
                        <TableHead className="w-12"></TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {fields.map((field, index) => (
                        <TableRow key={field.id}>
                          <TableCell>
                            {vendorBills.length > 0 ? (
                              <Controller
                                name={`allocations.${index}.bill_reference` as const}
                                control={control}
                                rules={{ required: t("accounting:billReferenceRequired", "Bill reference is required") }}
                                render={({ field: f }) => (
                                  <Select
                                    onValueChange={(val) => {
                                      f.onChange(val);
                                      const bill = vendorBills.find((b) => b.bill_number === val);
                                      if (bill) {
                                        setValue(`allocations.${index}.bill_date`, bill.bill_date);
                                        setValue(`allocations.${index}.bill_amount`, bill.total_amount);
                                        if (!allocations[index]?.amount) {
                                          setValue(`allocations.${index}.amount`, bill.total_amount);
                                        }
                                      }
                                    }}
                                    value={f.value}
                                  >
                                    <SelectTrigger>
                                      <SelectValue placeholder={t("accounting:selectBill", "Select bill")} />
                                    </SelectTrigger>
                                    <SelectContent>
                                      {vendorBills.map((bill) => (
                                        <SelectItem key={bill.id} value={bill.bill_number}>
                                          {bill.bill_number} — {formatCurrency(bill.total_amount)} ({bill.bill_date})
                                        </SelectItem>
                                      ))}
                                    </SelectContent>
                                  </Select>
                                )}
                              />
                            ) : (
                              <Input
                                {...register(`allocations.${index}.bill_reference` as const, {
                                  required: t("accounting:billReferenceRequired", "Bill reference is required"),
                                })}
                                placeholder={t("accounting:billReferencePlaceholder", "INV-001, PO-123, etc.")}
                              />
                            )}
                          </TableCell>
                          <TableCell>
                            <Input
                              type="date"
                              {...register(`allocations.${index}.bill_date` as const)}
                              readOnly={vendorBills.length > 0}
                            />
                          </TableCell>
                          <TableCell>
                            <Input
                              type="number"
                              step="0.01"
                              min="0"
                              {...register(`allocations.${index}.bill_amount` as const)}
                              placeholder="0.00"
                              className="text-right"
                              readOnly={vendorBills.length > 0}
                            />
                          </TableCell>
                          <TableCell>
                            <Input
                              type="number"
                              step="0.01"
                              min="0.01"
                              {...register(`allocations.${index}.amount` as const, {
                                required: t("accounting:amountRequired", "Amount is required"),
                                validate: (v) => parseFloat(v) > 0 || t("accounting:amountPositive", "Amount must be positive"),
                              })}
                              placeholder="0.00"
                              className="text-right"
                            />
                          </TableCell>
                          <TableCell>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() => remove(index)}
                              className="text-destructive hover:text-destructive"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>

                {/* Allocation Summary */}
                <div className="mt-4 flex justify-end">
                  <div className="w-64 space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">
                        {t("accounting:paymentAmount", "Payment Amount")}:
                      </span>
                      <span className="ltr-number">{formatCurrency(paymentAmount || "0")}</span>
                    </div>
                    <div className="flex justify-between font-medium">
                      <span>{t("accounting:totalAllocated", "Total Allocated")}:</span>
                      <span className="ltr-number">{formatCurrency(totalAllocated)}</span>
                    </div>
                    <div className="flex justify-between border-t pt-2">
                      <span className="text-muted-foreground">
                        {t("accounting:unallocated", "Unallocated")}:
                      </span>
                      <span className={cn("ltr-number", unallocatedAmount > 0 && "text-amber-600")}>
                        {formatCurrency(unallocatedAmount)}
                      </span>
                    </div>
                  </div>
                </div>
              </>
            )}
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
