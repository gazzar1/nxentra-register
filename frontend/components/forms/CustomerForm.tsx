import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "next-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAccounts } from "@/queries/useAccounts";
import type { Customer, CustomerCreatePayload, CustomerUpdatePayload } from "@/types/account";

const customerSchema = z.object({
  code: z.string().min(1, "Customer code is required").max(50),
  name: z.string().min(1, "Customer name is required").max(255),
  name_ar: z.string().max(255).optional(),
  email: z.string().email().optional().or(z.literal("")),
  phone: z.string().max(50).optional(),
  address: z.string().max(500).optional(),
  address_ar: z.string().max(500).optional(),
  default_ar_account_id: z.number().nullable().optional(),
  credit_limit: z.string().optional(),
  payment_terms_days: z.number().min(0).max(365).optional(),
  currency: z.string().length(3).optional(),
  tax_id: z.string().max(50).optional(),
  notes: z.string().max(1000).optional(),
  notes_ar: z.string().max(1000).optional(),
  status: z.enum(["ACTIVE", "INACTIVE", "BLOCKED"]).optional(),
});

type CustomerFormData = z.infer<typeof customerSchema>;

interface CustomerFormProps {
  initialData?: Partial<Customer>;
  onSubmit: (data: Record<string, unknown>) => Promise<void>;
  isSubmitting?: boolean;
  onCancel?: () => void;
  isEdit?: boolean;
}

export function CustomerForm({
  initialData,
  onSubmit,
  isSubmitting,
  onCancel,
  isEdit = false,
}: CustomerFormProps) {
  const { t } = useTranslation(["common", "accounting"]);
  const { data: accounts } = useAccounts();

  // Filter to only show AR control accounts
  const arAccounts = accounts?.filter(
    (a) => a.is_postable && (a.role === "RECEIVABLE_CONTROL" || a.account_type === "RECEIVABLE")
  ) || [];

  const form = useForm<CustomerFormData>({
    resolver: zodResolver(customerSchema),
    defaultValues: {
      code: initialData?.code || "",
      name: initialData?.name || "",
      name_ar: initialData?.name_ar || "",
      email: initialData?.email || "",
      phone: initialData?.phone || "",
      address: initialData?.address || "",
      address_ar: initialData?.address_ar || "",
      default_ar_account_id: initialData?.default_ar_account || null,
      credit_limit: initialData?.credit_limit || "",
      payment_terms_days: initialData?.payment_terms_days || 30,
      currency: initialData?.currency || "USD",
      tax_id: initialData?.tax_id || "",
      notes: initialData?.notes || "",
      notes_ar: initialData?.notes_ar || "",
      status: initialData?.status || "ACTIVE",
    },
  });

  const handleSubmit = async (data: CustomerFormData) => {
    await onSubmit({
      code: data.code,
      name: data.name,
      name_ar: data.name_ar || undefined,
      email: data.email || undefined,
      phone: data.phone || undefined,
      address: data.address || undefined,
      address_ar: data.address_ar || undefined,
      default_ar_account_id: data.default_ar_account_id || undefined,
      credit_limit: data.credit_limit || undefined,
      payment_terms_days: data.payment_terms_days,
      currency: data.currency || undefined,
      tax_id: data.tax_id || undefined,
      notes: data.notes || undefined,
      notes_ar: data.notes_ar || undefined,
      ...(isEdit && data.status ? { status: data.status } : {}),
    });
  };

  return (
    <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Code */}
        <div className="space-y-2">
          <Label htmlFor="code">Customer Code *</Label>
          <Input
            id="code"
            {...form.register("code")}
            placeholder="CUST001"
            className="font-mono ltr-code"
            disabled={isEdit}
          />
          {form.formState.errors.code && (
            <p className="text-sm text-destructive">{form.formState.errors.code.message}</p>
          )}
        </div>

        {/* Status (edit only) */}
        {isEdit && (
          <div className="space-y-2">
            <Label htmlFor="status">Status</Label>
            <Select
              value={form.watch("status")}
              onValueChange={(value) => form.setValue("status", value as any)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ACTIVE">Active</SelectItem>
                <SelectItem value="INACTIVE">Inactive</SelectItem>
                <SelectItem value="BLOCKED">Blocked</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}

        {/* Name */}
        <div className="space-y-2">
          <Label htmlFor="name">Name (English) *</Label>
          <Input id="name" {...form.register("name")} placeholder="Customer name" />
          {form.formState.errors.name && (
            <p className="text-sm text-destructive">{form.formState.errors.name.message}</p>
          )}
        </div>

        {/* Name Arabic */}
        <div className="space-y-2">
          <Label htmlFor="name_ar">Name (Arabic)</Label>
          <Input id="name_ar" {...form.register("name_ar")} placeholder="اسم العميل" dir="rtl" />
        </div>

        {/* Email */}
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" {...form.register("email")} placeholder="customer@example.com" />
          {form.formState.errors.email && (
            <p className="text-sm text-destructive">{form.formState.errors.email.message}</p>
          )}
        </div>

        {/* Phone */}
        <div className="space-y-2">
          <Label htmlFor="phone">Phone</Label>
          <Input id="phone" {...form.register("phone")} placeholder="+1 234 567 8900" />
        </div>

        {/* Default AR Account */}
        <div className="space-y-2">
          <Label htmlFor="default_ar_account_id">Default AR Account</Label>
          <Select
            value={form.watch("default_ar_account_id")?.toString() || "__none__"}
            onValueChange={(value) =>
              form.setValue("default_ar_account_id", value === "__none__" ? null : parseInt(value))
            }
          >
            <SelectTrigger>
              <SelectValue placeholder="Select AR account" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">None (use company default)</SelectItem>
              {arAccounts.map((account) => (
                <SelectItem key={account.id} value={account.id.toString()}>
                  <span className="font-mono ltr-code">{account.code}</span> - {account.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            The default receivables account for this customer
          </p>
        </div>

        {/* Credit Limit */}
        <div className="space-y-2">
          <Label htmlFor="credit_limit">Credit Limit</Label>
          <Input
            id="credit_limit"
            {...form.register("credit_limit")}
            placeholder="10000.00"
            className="font-mono ltr-number"
          />
        </div>

        {/* Payment Terms */}
        <div className="space-y-2">
          <Label htmlFor="payment_terms_days">Payment Terms (days)</Label>
          <Input
            id="payment_terms_days"
            type="number"
            {...form.register("payment_terms_days", { valueAsNumber: true })}
            placeholder="30"
          />
        </div>

        {/* Currency */}
        <div className="space-y-2">
          <Label htmlFor="currency">Currency</Label>
          <Select
            value={form.watch("currency") || "USD"}
            onValueChange={(value) => form.setValue("currency", value)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="USD">USD - US Dollar</SelectItem>
              <SelectItem value="EUR">EUR - Euro</SelectItem>
              <SelectItem value="GBP">GBP - British Pound</SelectItem>
              <SelectItem value="SAR">SAR - Saudi Riyal</SelectItem>
              <SelectItem value="AED">AED - UAE Dirham</SelectItem>
              <SelectItem value="EGP">EGP - Egyptian Pound</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Tax ID */}
        <div className="space-y-2">
          <Label htmlFor="tax_id">Tax ID / VAT Number</Label>
          <Input id="tax_id" {...form.register("tax_id")} placeholder="123456789" />
        </div>
      </div>

      {/* Address */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-2">
          <Label htmlFor="address">Address (English)</Label>
          <textarea
            id="address"
            {...form.register("address")}
            className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            placeholder="Full address"
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="address_ar">Address (Arabic)</Label>
          <textarea
            id="address_ar"
            {...form.register("address_ar")}
            dir="rtl"
            className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            placeholder="العنوان الكامل"
          />
        </div>
      </div>

      {/* Notes */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-2">
          <Label htmlFor="notes">Notes (English)</Label>
          <textarea
            id="notes"
            {...form.register("notes")}
            className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="notes_ar">Notes (Arabic)</Label>
          <textarea
            id="notes_ar"
            {...form.register("notes_ar")}
            dir="rtl"
            className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          />
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-4">
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? t("actions.loading") : t("actions.save")}
        </Button>
        {onCancel && (
          <Button type="button" variant="outline" onClick={onCancel}>
            {t("actions.cancel")}
          </Button>
        )}
      </div>
    </form>
  );
}
