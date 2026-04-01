import React from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Plus, Trash2, Save } from "lucide-react";
import { useForm, useFieldArray, Controller } from "react-hook-form";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { CompanyDateInput } from "@/components/ui/CompanyDateInput";
import { FormattedAmountInput } from "@/components/ui/FormattedAmountInput";
import { PageHeader } from "@/components/common";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { useVendors, useAccounts } from "@/queries/useAccounts";
import { useItems, useTaxCodes, usePostingProfiles } from "@/queries/useSales";
import { useCreatePurchaseOrder } from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/AuthContext";

interface POLineForm {
  item_id: string;
  description: string;
  quantity: string;
  unit_price: string;
  discount_amount: string;
  tax_code_id: string;
  account_id: string;
}

interface POForm {
  order_date: string;
  expected_delivery_date: string;
  vendor_id: string;
  posting_profile_id: string;
  reference: string;
  notes: string;
  lines: POLineForm[];
}

export default function NewPurchaseOrderPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { company } = useAuth();
  const { data: vendors } = useVendors();
  const { data: items } = useItems();
  const { data: taxCodes } = useTaxCodes({ direction: "INPUT" });
  const { data: postingProfiles } = usePostingProfiles({ profile_type: "VENDOR" });
  const { data: accounts } = useAccounts();
  const createPO = useCreatePurchaseOrder();

  const companyFmt = company ? {
    thousand_separator: company.thousand_separator,
    decimal_separator: company.decimal_separator,
    decimal_places: company.decimal_places,
  } : undefined;

  const expenseAccounts = accounts?.filter(
    (a) => (a.account_type === "EXPENSE" || a.account_type === "ASSET") && a.is_postable && !a.is_header
  );

  const { register, control, handleSubmit, watch, setValue, formState: { errors, isSubmitting } } = useForm<POForm>({
    defaultValues: {
      order_date: new Date().toISOString().split("T")[0],
      expected_delivery_date: "",
      vendor_id: "",
      posting_profile_id: "",
      reference: "",
      notes: "",
      lines: [{ item_id: "", description: "", quantity: "1", unit_price: "0", discount_amount: "0", tax_code_id: "", account_id: "" }],
    },
  });

  const { fields, append, remove } = useFieldArray({ control, name: "lines" });
  const watchLines = watch("lines");

  const calculateLineTotal = (line: POLineForm) => {
    const qty = parseFloat(line.quantity) || 0;
    const price = parseFloat(line.unit_price) || 0;
    const discount = parseFloat(line.discount_amount) || 0;
    const gross = qty * price;
    const net = gross - discount;
    const taxCode = taxCodes?.find((tc) => tc.id.toString() === line.tax_code_id);
    const taxRate = taxCode ? parseFloat(taxCode.rate) : 0;
    const tax = net * taxRate;
    return { gross, net, tax, total: net + tax };
  };

  const totals = watchLines.reduce(
    (acc, line) => {
      const c = calculateLineTotal(line);
      return { subtotal: acc.subtotal + c.gross, totalDiscount: acc.totalDiscount + (parseFloat(line.discount_amount) || 0), totalTax: acc.totalTax + c.tax, totalAmount: acc.totalAmount + c.total };
    },
    { subtotal: 0, totalDiscount: 0, totalTax: 0, totalAmount: 0 }
  );

  const handleItemChange = (index: number, itemId: string) => {
    const item = items?.find((i) => i.id.toString() === itemId);
    if (item) {
      setValue(`lines.${index}.description`, item.name);
      setValue(`lines.${index}.unit_price`, item.default_cost || item.default_unit_price);
      if (item.purchase_account) setValue(`lines.${index}.account_id`, item.purchase_account.toString());
      if (item.default_tax_code) {
        const tc = taxCodes?.find((t) => t.id === item.default_tax_code);
        if (tc) setValue(`lines.${index}.tax_code_id`, item.default_tax_code.toString());
      }
    }
  };

  const onSubmit = async (data: POForm) => {
    try {
      await createPO.mutateAsync({
        vendor_id: parseInt(data.vendor_id),
        posting_profile_id: parseInt(data.posting_profile_id),
        order_date: data.order_date,
        expected_delivery_date: data.expected_delivery_date || undefined,
        reference: data.reference,
        notes: data.notes,
        lines: data.lines.map((line) => ({
          item_id: line.item_id ? parseInt(line.item_id) : undefined,
          description: line.description,
          quantity: line.quantity,
          unit_price: line.unit_price,
          discount_amount: line.discount_amount || "0",
          tax_code_id: line.tax_code_id ? parseInt(line.tax_code_id) : undefined,
          account_id: parseInt(line.account_id),
        })),
      });
      toast({ title: "Purchase order created", description: "PO has been created as a draft." });
      router.push("/accounting/purchase-orders");
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to create PO.", variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <PageHeader title="New Purchase Order" subtitle="Create a new purchase order" actions={
          <div className="flex gap-2">
            <Link href="/accounting/purchase-orders"><Button type="button" variant="outline"><ArrowLeft className="h-4 w-4 me-2" />Cancel</Button></Link>
            <Button type="submit" disabled={isSubmitting}><Save className="h-4 w-4 me-2" />{isSubmitting ? "Saving..." : "Save Draft"}</Button>
          </div>
        } />

        <Card>
          <CardHeader><CardTitle>Order Details</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="space-y-2">
              <Label>PO Number</Label>
              <Input value="Auto-generated" disabled className="bg-muted" />
            </div>
            <div className="space-y-2">
              <Label>Order Date *</Label>
              <CompanyDateInput id="order_date" value={watch("order_date")} onChange={(iso) => setValue("order_date", iso, { shouldValidate: true })} dateFormat={(company?.date_format as any) || "YYYY-MM-DD"} />
            </div>
            <div className="space-y-2">
              <Label>Expected Delivery</Label>
              <CompanyDateInput id="expected_delivery_date" value={watch("expected_delivery_date") || ""} onChange={(iso) => setValue("expected_delivery_date", iso)} dateFormat={(company?.date_format as any) || "YYYY-MM-DD"} />
            </div>
            <div className="space-y-2">
              <Label>Vendor *</Label>
              <Controller name="vendor_id" control={control} rules={{ required: "Vendor is required" }} render={({ field }) => (
                <Select onValueChange={field.onChange} value={field.value}>
                  <SelectTrigger><SelectValue placeholder="Select vendor" /></SelectTrigger>
                  <SelectContent>{vendors?.map((v) => <SelectItem key={v.id} value={v.id.toString()}>{v.code} - {v.name}</SelectItem>)}</SelectContent>
                </Select>
              )} />
              {errors.vendor_id && <p className="text-sm text-destructive">{errors.vendor_id.message}</p>}
            </div>
            <div className="space-y-2">
              <Label>Posting Profile *</Label>
              <Controller name="posting_profile_id" control={control} rules={{ required: "Required" }} render={({ field }) => (
                <Select onValueChange={field.onChange} value={field.value}>
                  <SelectTrigger><SelectValue placeholder="Select profile" /></SelectTrigger>
                  <SelectContent>{postingProfiles?.map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.code} - {p.name}</SelectItem>)}</SelectContent>
                </Select>
              )} />
            </div>
            <div className="space-y-2">
              <Label>Reference</Label>
              <Input {...register("reference")} placeholder="External reference" />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>Notes</Label>
              <Textarea {...register("notes")} placeholder="Internal notes..." rows={2} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Line Items</CardTitle>
            <Button type="button" variant="outline" size="sm" onClick={() => append({ item_id: "", description: "", quantity: "1", unit_price: "0", discount_amount: "0", tax_code_id: "", account_id: "" })}>
              <Plus className="h-4 w-4 me-2" />Add Line
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
                          <Controller name={`lines.${index}.item_id`} control={control} render={({ field: f }) => (
                            <Select onValueChange={(val) => { f.onChange(val); handleItemChange(index, val); }} value={f.value}>
                              <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Select" /></SelectTrigger>
                              <SelectContent>{items?.map((item) => <SelectItem key={item.id} value={item.id.toString()}>{item.code}</SelectItem>)}</SelectContent>
                            </Select>
                          )} />
                        </td>
                        <td className="py-2 px-2"><Input {...register(`lines.${index}.description`, { required: true })} className="h-8 text-xs" placeholder="Description" /></td>
                        <td className="py-2 px-2">
                          <Controller name={`lines.${index}.account_id`} control={control} rules={{ required: true }} render={({ field: f }) => (
                            <Select onValueChange={f.onChange} value={f.value}>
                              <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Account" /></SelectTrigger>
                              <SelectContent>{expenseAccounts?.map((acc) => <SelectItem key={acc.id} value={acc.id.toString()}>{acc.code}</SelectItem>)}</SelectContent>
                            </Select>
                          )} />
                        </td>
                        <td className="py-2 px-2"><Input {...register(`lines.${index}.quantity`)} type="number" step="0.0001" min="0" className="h-8 text-xs text-end" /></td>
                        <td className="py-2 px-2">
                          <FormattedAmountInput value={parseFloat(watchLines[index]?.unit_price) || 0} onChange={(v) => setValue(`lines.${index}.unit_price`, String(v))} settings={companyFmt} className="h-8 text-xs text-end" />
                        </td>
                        <td className="py-2 px-2">
                          <FormattedAmountInput value={parseFloat(watchLines[index]?.discount_amount) || 0} onChange={(v) => setValue(`lines.${index}.discount_amount`, String(v))} settings={companyFmt} className="h-8 text-xs text-end" />
                        </td>
                        <td className="py-2 px-2">
                          <Controller name={`lines.${index}.tax_code_id`} control={control} render={({ field: f }) => (
                            <Select onValueChange={(val) => f.onChange(val === "_none" ? "" : val)} value={f.value || "_none"}>
                              <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Tax" /></SelectTrigger>
                              <SelectContent>
                                <SelectItem value="_none">None</SelectItem>
                                {taxCodes?.map((tc) => <SelectItem key={tc.id} value={tc.id.toString()}>{tc.code} ({(parseFloat(tc.rate) * 100).toFixed(0)}%)</SelectItem>)}
                              </SelectContent>
                            </Select>
                          )} />
                        </td>
                        <td className="py-2 px-2 text-end font-mono text-sm">{lineCalc.total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                        <td className="py-2 px-2">{fields.length > 1 && <Button type="button" variant="ghost" size="sm" onClick={() => remove(index)} className="h-8 w-8 p-0"><Trash2 className="h-4 w-4 text-destructive" /></Button>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="flex justify-end mt-6">
              <div className="w-64 space-y-2">
                <div className="flex justify-between text-sm"><span className="text-muted-foreground">Subtotal</span><span className="font-mono">{totals.subtotal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div>
                <div className="flex justify-between text-sm"><span className="text-muted-foreground">Discount</span><span className="font-mono text-red-600">-{totals.totalDiscount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div>
                <div className="flex justify-between text-sm"><span className="text-muted-foreground">Tax</span><span className="font-mono">{totals.totalTax.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div>
                <div className="border-t pt-2 flex justify-between font-semibold"><span>Total</span><span className="font-mono">{totals.totalAmount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></div>
              </div>
            </div>
          </CardContent>
        </Card>
      </form>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
