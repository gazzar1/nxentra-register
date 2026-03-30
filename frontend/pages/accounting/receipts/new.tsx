import { GetServerSideProps } from "next";
import { useState, useEffect, useMemo } from "react";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Save, FileText } from "lucide-react";
import { useForm, Controller } from "react-hook-form";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { CompanyDateInput } from "@/components/ui/CompanyDateInput";
import { FormattedAmountInput } from "@/components/ui/FormattedAmountInput";
import { PageHeader, LoadingSpinner } from "@/components/common";
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
import { useCustomers, useAccounts } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import {
  customerReceiptsService,
  type CustomerReceiptCreatePayload,
  type OpenInvoice,
  type ReceiptAllocation,
} from "@/services/accounts.service";
import { periodsService, type FiscalPeriod } from "@/services/periods.service";
import { exchangeRatesService } from "@/services/exchange-rates.service";
import { useCompanySettings } from "@/queries/useCompanySettings";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

interface ReceiptFormData {
  customer_id: string;
  receipt_date: string;
  accounting_date: string;
  amount: string;
  bank_account_id: string;
  ar_control_account_id: string;
  reference: string;
  memo: string;
}

interface InvoiceAllocationState {
  [invoicePublicId: string]: {
    selected: boolean;
    amount: string;
  };
}

export default function NewCustomerReceiptPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();
  const router = useRouter();
  const { toast } = useToast();
  const { data: customers } = useCustomers();
  const { data: accounts } = useAccounts();

  // State for open invoices
  const [openInvoices, setOpenInvoices] = useState<OpenInvoice[]>([]);
  const [loadingInvoices, setLoadingInvoices] = useState(false);
  const [allocations, setAllocations] = useState<InvoiceAllocationState>({});
  const [totalOutstanding, setTotalOutstanding] = useState("0.00");
  const [periods, setPeriods] = useState<FiscalPeriod[]>([]);
  const [resolvedPeriod, setResolvedPeriod] = useState<string>("");
  const [receiptCurrency, setReceiptCurrency] = useState<string>("");
  const [exchangeRate, setExchangeRate] = useState<string>("1");
  const [availableCurrencies, setAvailableCurrencies] = useState<string[]>([]);
  const { data: companySettings } = useCompanySettings();
  const { company } = useAuth();
  const companyFmt = company ? {
    thousand_separator: company.thousand_separator,
    decimal_separator: company.decimal_separator,
    decimal_places: company.decimal_places,
  } : undefined;
  const functionalCurrency = companySettings?.functional_currency || companySettings?.default_currency || "USD";

  // Filter accounts by role
  const bankAccounts = accounts?.filter(
    (a) => a.role === "LIQUIDITY" && a.is_postable && !a.is_header
  );
  const arControlAccounts = accounts?.filter(
    (a) => a.role === "RECEIVABLE_CONTROL" && a.is_postable && !a.is_header
  );

  const {
    register,
    control,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<ReceiptFormData>({
    defaultValues: {
      customer_id: "",
      receipt_date: new Date().toISOString().split("T")[0],
      accounting_date: new Date().toISOString().split("T")[0],
      amount: "",
      bank_account_id: "",
      ar_control_account_id: "",
      reference: "",
      memo: "",
    },
  });

  const selectedCustomerId = watch("customer_id");
  const receiptAmount = watch("amount");
  const watchReceiptDate = watch("receipt_date");
  const watchAccountingDate = watch("accounting_date");

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

  // Auto-set currency from customer
  useEffect(() => {
    if (selectedCustomerId && customers) {
      const cust = customers.find((c) => String(c.id) === selectedCustomerId);
      if (cust?.currency) {
        setReceiptCurrency(cust.currency);
      } else {
        setReceiptCurrency(functionalCurrency);
      }
    }
  }, [selectedCustomerId, customers, functionalCurrency]);

  // Auto-lookup exchange rate when currency or date changes
  useEffect(() => {
    if (!receiptCurrency || receiptCurrency === functionalCurrency) {
      setExchangeRate("1");
      return;
    }
    const dateStr = watchReceiptDate || new Date().toISOString().split("T")[0];
    exchangeRatesService
      .lookup({ from_currency: receiptCurrency, to_currency: functionalCurrency, date: dateStr })
      .then((res) => {
        if (res.data.rate) {
          setExchangeRate(res.data.rate);
        }
      })
      .catch(() => {});
  }, [receiptCurrency, watchReceiptDate, functionalCurrency]);

  // Sync accounting_date with receipt_date when receipt_date changes
  useEffect(() => {
    if (watchReceiptDate) {
      setValue("accounting_date", watchReceiptDate);
    }
  }, [watchReceiptDate, setValue]);

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
    return Object.values(allocations)
      .filter((a) => a.selected && parseFloat(a.amount) > 0)
      .reduce((sum, a) => sum + parseFloat(a.amount || "0"), 0);
  }, [allocations]);

  // Calculate unallocated amount
  const unallocatedAmount = useMemo(() => {
    const receipt = parseFloat(receiptAmount || "0");
    return Math.max(0, receipt - totalAllocated);
  }, [receiptAmount, totalAllocated]);

  // Load open invoices when customer changes
  useEffect(() => {
    if (selectedCustomerId) {
      loadOpenInvoices(parseInt(selectedCustomerId));
    } else {
      setOpenInvoices([]);
      setAllocations({});
      setTotalOutstanding("0.00");
    }
  }, [selectedCustomerId]);

  const loadOpenInvoices = async (customerId: number) => {
    setLoadingInvoices(true);
    try {
      const response = await customerReceiptsService.getOpenInvoices(customerId);
      setOpenInvoices(response.data.open_invoices);
      setTotalOutstanding(response.data.total_outstanding);

      // Initialize allocations state
      const initialAllocations: InvoiceAllocationState = {};
      response.data.open_invoices.forEach((inv) => {
        initialAllocations[inv.public_id] = {
          selected: false,
          amount: inv.amount_due,
        };
      });
      setAllocations(initialAllocations);
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to load open invoices.",
        variant: "destructive",
      });
    } finally {
      setLoadingInvoices(false);
    }
  };

  const handleInvoiceToggle = (invoicePublicId: string, checked: boolean) => {
    setAllocations((prev) => ({
      ...prev,
      [invoicePublicId]: {
        ...prev[invoicePublicId],
        selected: checked,
      },
    }));
  };

  const handleAllocationAmountChange = (invoicePublicId: string, amount: string) => {
    const invoice = openInvoices.find((i) => i.public_id === invoicePublicId);
    const maxAmount = invoice ? parseFloat(invoice.amount_due) : 0;
    const newAmount = Math.min(parseFloat(amount || "0"), maxAmount);

    setAllocations((prev) => ({
      ...prev,
      [invoicePublicId]: {
        ...prev[invoicePublicId],
        amount: newAmount.toFixed(2),
        selected: newAmount > 0,
      },
    }));
  };

  const handleApplyToAll = () => {
    let remainingAmount = parseFloat(receiptAmount || "0");
    const newAllocations: InvoiceAllocationState = {};

    // Sort invoices by date (oldest first)
    const sortedInvoices = [...openInvoices].sort(
      (a, b) => new Date(a.invoice_date).getTime() - new Date(b.invoice_date).getTime()
    );

    for (const invoice of sortedInvoices) {
      const amountDue = parseFloat(invoice.amount_due);
      if (remainingAmount <= 0) {
        newAllocations[invoice.public_id] = {
          selected: false,
          amount: invoice.amount_due,
        };
      } else {
        const allocAmount = Math.min(amountDue, remainingAmount);
        newAllocations[invoice.public_id] = {
          selected: allocAmount > 0,
          amount: allocAmount.toFixed(2),
        };
        remainingAmount -= allocAmount;
      }
    }

    setAllocations(newAllocations);
  };

  const onSubmit = async (data: ReceiptFormData) => {
    // Build allocations array from selected invoices
    const selectedAllocations: ReceiptAllocation[] = Object.entries(allocations)
      .filter(([_, alloc]) => alloc.selected && parseFloat(alloc.amount) > 0)
      .map(([invoicePublicId, alloc]) => ({
        invoice_public_id: invoicePublicId,
        amount: alloc.amount,
      }));

    // Validate total allocated doesn't exceed receipt amount
    if (totalAllocated > parseFloat(data.amount)) {
      toast({
        title: "Allocation Error",
        description: "Total allocated amount exceeds receipt amount.",
        variant: "destructive",
      });
      return;
    }

    try {
      const payload: CustomerReceiptCreatePayload = {
        customer_id: parseInt(data.customer_id),
        receipt_date: data.receipt_date,
        accounting_date: data.accounting_date,
        amount: data.amount,
        bank_account_id: parseInt(data.bank_account_id),
        ar_control_account_id: parseInt(data.ar_control_account_id),
        reference: data.reference,
        memo: data.memo,
        allocations: selectedAllocations.length > 0 ? selectedAllocations : undefined,
        currency: receiptCurrency || functionalCurrency,
        exchange_rate: exchangeRate,
      };

      await customerReceiptsService.create(payload);
      toast({
        title: t("accounting:receiptRecorded", "Receipt recorded"),
        description: selectedAllocations.length > 0
          ? t("accounting:receiptWithAllocations", `Receipt recorded and applied to ${selectedAllocations.length} invoice(s).`)
          : t("accounting:receiptRecordedSuccess", "Customer receipt has been recorded successfully."),
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
              <CompanyDateInput
                id="receipt_date"
                value={watch("receipt_date")}
                onChange={(iso) => setValue("receipt_date", iso, { shouldValidate: true })}
                dateFormat={(company?.date_format as any) || "YYYY-MM-DD"}
              />
              {errors.receipt_date && (
                <p className="text-sm text-destructive">{errors.receipt_date.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="accounting_date">{t("accounting:accountingDate", "Accounting Date")} *</Label>
              <CompanyDateInput
                id="accounting_date"
                value={watch("accounting_date")}
                onChange={(iso) => setValue("accounting_date", iso, { shouldValidate: true })}
                dateFormat={(company?.date_format as any) || "YYYY-MM-DD"}
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
              <Select value={receiptCurrency} onValueChange={setReceiptCurrency}>
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
              <FormattedAmountInput
                value={parseFloat(watch("amount")) || 0}
                onChange={(v) => setValue("amount", String(v), { shouldValidate: true })}
                settings={companyFmt}
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
                disabled={receiptCurrency === functionalCurrency || !receiptCurrency}
              />
              {receiptCurrency && receiptCurrency !== functionalCurrency && (
                <p className="text-xs text-muted-foreground">
                  1 {receiptCurrency} = {exchangeRate} {functionalCurrency}
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

        {/* Invoice Allocation Section */}
        {selectedCustomerId && (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <FileText className="h-5 w-5" />
                    {t("accounting:invoiceAllocation", "Invoice Allocation")}
                  </CardTitle>
                  <CardDescription>
                    {t("accounting:invoiceAllocationDescription", "Optionally apply this receipt to specific open invoices")}
                  </CardDescription>
                </div>
                {openInvoices.length > 0 && receiptAmount && (
                  <Button type="button" variant="outline" size="sm" onClick={handleApplyToAll}>
                    {t("accounting:applyToOldest", "Apply to Oldest First")}
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent>
              {loadingInvoices ? (
                <div className="flex justify-center py-8">
                  <LoadingSpinner />
                </div>
              ) : openInvoices.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  {t("accounting:noOpenInvoices", "No open invoices found for this customer.")}
                </p>
              ) : (
                <>
                  <div className="rounded-md border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-12"></TableHead>
                          <TableHead>{t("accounting:invoiceNumber", "Invoice #")}</TableHead>
                          <TableHead>{t("accounting:invoiceDate", "Date")}</TableHead>
                          <TableHead>{t("accounting:dueDate", "Due Date")}</TableHead>
                          <TableHead className="text-right">{t("accounting:total", "Total")}</TableHead>
                          <TableHead className="text-right">{t("accounting:amountDue", "Due")}</TableHead>
                          <TableHead className="text-right w-32">{t("accounting:applyAmount", "Apply")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {openInvoices.map((invoice) => {
                          const allocation = allocations[invoice.public_id];
                          const isOverdue = invoice.due_date && new Date(invoice.due_date) < new Date();
                          return (
                            <TableRow key={invoice.public_id}>
                              <TableCell>
                                <Checkbox
                                  checked={allocation?.selected || false}
                                  onCheckedChange={(checked) =>
                                    handleInvoiceToggle(invoice.public_id, checked as boolean)
                                  }
                                />
                              </TableCell>
                              <TableCell className="font-medium">{invoice.invoice_number}</TableCell>
                              <TableCell>{invoice.invoice_date}</TableCell>
                              <TableCell className={cn(isOverdue && "text-red-600")}>
                                {invoice.due_date || "-"}
                              </TableCell>
                              <TableCell className="text-right ltr-number">
                                {formatCurrency(invoice.total_amount)}
                              </TableCell>
                              <TableCell className="text-right ltr-number font-medium">
                                {formatCurrency(invoice.amount_due)}
                              </TableCell>
                              <TableCell>
                                <FormattedAmountInput
                                  value={parseFloat(allocation?.amount) || 0}
                                  onChange={(v) =>
                                    handleAllocationAmountChange(invoice.public_id, String(v))
                                  }
                                  settings={companyFmt}
                                  className="text-right w-28"
                                  disabled={!allocation?.selected}
                                />
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>

                  {/* Allocation Summary */}
                  <div className="mt-4 flex justify-end">
                    <div className="w-64 space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">
                          {t("accounting:totalOutstanding", "Total Outstanding")}:
                        </span>
                        <span className="ltr-number">{formatCurrency(totalOutstanding)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">
                          {t("accounting:receiptAmount", "Receipt Amount")}:
                        </span>
                        <span className="ltr-number">{formatCurrency(receiptAmount || "0")}</span>
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
        )}

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
