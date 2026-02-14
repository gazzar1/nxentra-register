import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Plus, Trash2, Save } from "lucide-react";
import { useState } from "react";
import { useForm, useFieldArray, Controller } from "react-hook-form";
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
import { useCustomers } from "@/queries/useAccounts";
import { useItems, useTaxCodes, usePostingProfiles, useCreateSalesInvoice } from "@/queries/useSales";
import { useAccounts } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import type { SalesInvoiceCreatePayload, SalesInvoiceLineInput } from "@/types/sales";
import { cn } from "@/lib/cn";

interface InvoiceLineFormData {
  item_id: string;
  description: string;
  quantity: string;
  unit_price: string;
  discount_amount: string;
  tax_code_id: string;
  account_id: string;
}

interface InvoiceFormData {
  invoice_number: string;
  invoice_date: string;
  due_date: string;
  customer_id: string;
  posting_profile_id: string;
  notes: string;
  lines: InvoiceLineFormData[];
}

export default function NewSalesInvoicePage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: customers } = useCustomers();
  const { data: items } = useItems();
  const { data: taxCodes } = useTaxCodes({ direction: "OUTPUT" });
  const { data: postingProfiles } = usePostingProfiles({ profile_type: "CUSTOMER" });
  const { data: accounts } = useAccounts();
  const createInvoice = useCreateSalesInvoice();

  const revenueAccounts = accounts?.filter(
    (a) => a.account_type === "REVENUE" && a.is_postable && !a.is_header
  );

  const {
    register,
    control,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<InvoiceFormData>({
    defaultValues: {
      invoice_number: "",
      invoice_date: new Date().toISOString().split("T")[0],
      due_date: "",
      customer_id: "",
      posting_profile_id: "",
      notes: "",
      lines: [
        {
          item_id: "",
          description: "",
          quantity: "1",
          unit_price: "0",
          discount_amount: "0",
          tax_code_id: "",
          account_id: "",
        },
      ],
    },
  });

  const { fields, append, remove } = useFieldArray({
    control,
    name: "lines",
  });

  const watchLines = watch("lines");

  // Calculate line totals
  const calculateLineTotal = (line: InvoiceLineFormData) => {
    const qty = parseFloat(line.quantity) || 0;
    const price = parseFloat(line.unit_price) || 0;
    const discount = parseFloat(line.discount_amount) || 0;
    const gross = qty * price;
    const net = gross - discount;

    const taxCode = taxCodes?.find((tc) => tc.id.toString() === line.tax_code_id);
    const taxRate = taxCode ? parseFloat(taxCode.rate) : 0;
    const tax = net * taxRate;
    const total = net + tax;

    return { gross, net, tax, total };
  };

  // Calculate invoice totals
  const totals = watchLines.reduce(
    (acc, line) => {
      const lineCalc = calculateLineTotal(line);
      return {
        subtotal: acc.subtotal + lineCalc.gross,
        totalDiscount: acc.totalDiscount + (parseFloat(line.discount_amount) || 0),
        totalTax: acc.totalTax + lineCalc.tax,
        totalAmount: acc.totalAmount + lineCalc.total,
      };
    },
    { subtotal: 0, totalDiscount: 0, totalTax: 0, totalAmount: 0 }
  );

  const handleItemChange = (index: number, itemId: string) => {
    const item = items?.find((i) => i.id.toString() === itemId);
    if (item) {
      setValue(`lines.${index}.description`, item.name);
      setValue(`lines.${index}.unit_price`, item.default_unit_price);
      if (item.sales_account) {
        setValue(`lines.${index}.account_id`, item.sales_account.toString());
      }
      if (item.default_tax_code) {
        setValue(`lines.${index}.tax_code_id`, item.default_tax_code.toString());
      }
    }
  };

  const onSubmit = async (data: InvoiceFormData) => {
    try {
      const payload: SalesInvoiceCreatePayload = {
        invoice_number: data.invoice_number,
        invoice_date: data.invoice_date,
        due_date: data.due_date || null,
        customer_id: parseInt(data.customer_id),
        posting_profile_id: parseInt(data.posting_profile_id),
        notes: data.notes,
        lines: data.lines.map((line) => ({
          item_id: line.item_id ? parseInt(line.item_id) : null,
          description: line.description,
          quantity: line.quantity,
          unit_price: line.unit_price,
          discount_amount: line.discount_amount || "0",
          tax_code_id: line.tax_code_id ? parseInt(line.tax_code_id) : null,
          account_id: parseInt(line.account_id),
        })),
      };

      await createInvoice.mutateAsync(payload);
      toast({
        title: "Invoice created",
        description: `Invoice ${data.invoice_number} has been created as a draft.`,
      });
      router.push("/accounting/sales-invoices");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.error || "Failed to create invoice.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <PageHeader
          title="New Sales Invoice"
          subtitle="Create a new sales invoice"
          actions={
            <div className="flex gap-2">
              <Link href="/accounting/sales-invoices">
                <Button type="button" variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />
                  Cancel
                </Button>
              </Link>
              <Button type="submit" disabled={isSubmitting}>
                <Save className="h-4 w-4 me-2" />
                {isSubmitting ? "Saving..." : "Save Draft"}
              </Button>
            </div>
          }
        />

        {/* Header Info */}
        <Card>
          <CardHeader>
            <CardTitle>Invoice Details</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="space-y-2">
              <Label htmlFor="invoice_number">Invoice Number *</Label>
              <Input
                id="invoice_number"
                {...register("invoice_number", { required: "Invoice number is required" })}
                placeholder="INV-0001"
              />
              {errors.invoice_number && (
                <p className="text-sm text-destructive">{errors.invoice_number.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="invoice_date">Invoice Date *</Label>
              <Input
                id="invoice_date"
                type="date"
                {...register("invoice_date", { required: "Invoice date is required" })}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="due_date">Due Date</Label>
              <Input id="due_date" type="date" {...register("due_date")} />
            </div>

            <div className="space-y-2">
              <Label htmlFor="customer_id">Customer *</Label>
              <Controller
                name="customer_id"
                control={control}
                rules={{ required: "Customer is required" }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select customer" />
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
              <Label htmlFor="posting_profile_id">Posting Profile *</Label>
              <Controller
                name="posting_profile_id"
                control={control}
                rules={{ required: "Posting profile is required" }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select profile" />
                    </SelectTrigger>
                    <SelectContent>
                      {postingProfiles?.map((profile) => (
                        <SelectItem key={profile.id} value={profile.id.toString()}>
                          {profile.code} - {profile.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.posting_profile_id && (
                <p className="text-sm text-destructive">{errors.posting_profile_id.message}</p>
              )}
            </div>

            <div className="space-y-2 md:col-span-2 lg:col-span-3">
              <Label htmlFor="notes">Notes</Label>
              <Textarea id="notes" {...register("notes")} placeholder="Internal notes..." rows={2} />
            </div>
          </CardContent>
        </Card>

        {/* Line Items */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Line Items</CardTitle>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                append({
                  item_id: "",
                  description: "",
                  quantity: "1",
                  unit_price: "0",
                  discount_amount: "0",
                  tax_code_id: "",
                  account_id: "",
                })
              }
            >
              <Plus className="h-4 w-4 me-2" />
              Add Line
            </Button>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b text-sm text-muted-foreground">
                    <th className="text-start py-2 px-2 w-[140px]">Item</th>
                    <th className="text-start py-2 px-2">Description</th>
                    <th className="text-start py-2 px-2 w-[100px]">Account</th>
                    <th className="text-end py-2 px-2 w-[80px]">Qty</th>
                    <th className="text-end py-2 px-2 w-[100px]">Unit Price</th>
                    <th className="text-end py-2 px-2 w-[80px]">Discount</th>
                    <th className="text-start py-2 px-2 w-[100px]">Tax</th>
                    <th className="text-end py-2 px-2 w-[100px]">Total</th>
                    <th className="w-[40px]"></th>
                  </tr>
                </thead>
                <tbody>
                  {fields.map((field, index) => {
                    const lineCalc = calculateLineTotal(watchLines[index]);
                    return (
                      <tr key={field.id} className="border-b">
                        <td className="py-2 px-2">
                          <Controller
                            name={`lines.${index}.item_id`}
                            control={control}
                            render={({ field: f }) => (
                              <Select
                                onValueChange={(val) => {
                                  f.onChange(val);
                                  handleItemChange(index, val);
                                }}
                                value={f.value}
                              >
                                <SelectTrigger className="h-8 text-xs">
                                  <SelectValue placeholder="Select" />
                                </SelectTrigger>
                                <SelectContent>
                                  {items?.map((item) => (
                                    <SelectItem key={item.id} value={item.id.toString()}>
                                      {item.code}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            )}
                          />
                        </td>
                        <td className="py-2 px-2">
                          <Input
                            {...register(`lines.${index}.description`, { required: true })}
                            className="h-8 text-xs"
                            placeholder="Description"
                          />
                        </td>
                        <td className="py-2 px-2">
                          <Controller
                            name={`lines.${index}.account_id`}
                            control={control}
                            rules={{ required: "Account required" }}
                            render={({ field: f }) => (
                              <Select onValueChange={f.onChange} value={f.value}>
                                <SelectTrigger className="h-8 text-xs">
                                  <SelectValue placeholder="Account" />
                                </SelectTrigger>
                                <SelectContent>
                                  {revenueAccounts?.map((acc) => (
                                    <SelectItem key={acc.id} value={acc.id.toString()}>
                                      {acc.code}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            )}
                          />
                        </td>
                        <td className="py-2 px-2">
                          <Input
                            {...register(`lines.${index}.quantity`)}
                            type="number"
                            step="0.0001"
                            min="0"
                            className="h-8 text-xs text-end"
                          />
                        </td>
                        <td className="py-2 px-2">
                          <Input
                            {...register(`lines.${index}.unit_price`)}
                            type="number"
                            step="0.01"
                            min="0"
                            className="h-8 text-xs text-end"
                          />
                        </td>
                        <td className="py-2 px-2">
                          <Input
                            {...register(`lines.${index}.discount_amount`)}
                            type="number"
                            step="0.01"
                            min="0"
                            className="h-8 text-xs text-end"
                          />
                        </td>
                        <td className="py-2 px-2">
                          <Controller
                            name={`lines.${index}.tax_code_id`}
                            control={control}
                            render={({ field: f }) => (
                              <Select
                                onValueChange={(val) => f.onChange(val === "_none" ? "" : val)}
                                value={f.value || "_none"}
                              >
                                <SelectTrigger className="h-8 text-xs">
                                  <SelectValue placeholder="Tax" />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="_none">None</SelectItem>
                                  {taxCodes?.map((tc) => (
                                    <SelectItem key={tc.id} value={tc.id.toString()}>
                                      {tc.code} ({(parseFloat(tc.rate) * 100).toFixed(0)}%)
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            )}
                          />
                        </td>
                        <td className="py-2 px-2 text-end font-mono text-sm">
                          {lineCalc.total.toLocaleString(undefined, {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}
                        </td>
                        <td className="py-2 px-2">
                          {fields.length > 1 && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() => remove(index)}
                              className="h-8 w-8 p-0"
                            >
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Totals */}
            <div className="flex justify-end mt-6">
              <div className="w-64 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Subtotal</span>
                  <span className="font-mono">
                    {totals.subtotal.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Discount</span>
                  <span className="font-mono text-red-600">
                    -{totals.totalDiscount.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Tax</span>
                  <span className="font-mono">
                    {totals.totalTax.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                </div>
                <div className="border-t pt-2 flex justify-between font-semibold">
                  <span>Total</span>
                  <span className="font-mono">
                    {totals.totalAmount.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                </div>
              </div>
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
