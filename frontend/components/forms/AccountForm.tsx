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
import type { Account, AccountCreatePayload, AccountType } from "@/types/account";

const accountSchema = z.object({
  code: z.string().min(1, "Account code is required").max(20),
  name: z.string().min(1, "Account name is required").max(255),
  name_ar: z.string().max(255).optional(),
  account_type: z.string().min(1, "Account type is required"),
  is_header: z.boolean(),
  parent: z.string().optional(),
  description: z.string().max(1000).optional(),
  description_ar: z.string().max(1000).optional(),
});

type AccountFormData = z.infer<typeof accountSchema>;

interface AccountFormProps {
  initialData?: Partial<Account>;
  onSubmit: (data: AccountCreatePayload) => Promise<void>;
  isSubmitting?: boolean;
  onCancel?: () => void;
  extraContent?: React.ReactNode;
}

const ACCOUNT_TYPES: AccountType[] = [
  "ASSET",
  "LIABILITY",
  "EQUITY",
  "REVENUE",
  "EXPENSE",
  "RECEIVABLE",
  "PAYABLE",
  "CONTRA_ASSET",
  "CONTRA_LIABILITY",
  "CONTRA_EQUITY",
  "CONTRA_REVENUE",
  "CONTRA_EXPENSE",
  "MEMO",
];

export function AccountForm({
  initialData,
  onSubmit,
  isSubmitting,
  onCancel,
  extraContent,
}: AccountFormProps) {
  const { t } = useTranslation(["common", "accounting"]);
  const { data: accounts } = useAccounts();

  const form = useForm<AccountFormData>({
    resolver: zodResolver(accountSchema),
    defaultValues: {
      code: initialData?.code || "",
      name: initialData?.name || "",
      name_ar: initialData?.name_ar || "",
      account_type: initialData?.account_type || "",
      is_header: initialData?.is_header || false,
      parent: initialData?.parent_code || "",
      description: initialData?.description || "",
      description_ar: initialData?.description_ar || "",
    },
  });

  const handleSubmit = async (data: AccountFormData) => {
    await onSubmit({
      code: data.code,
      name: data.name,
      name_ar: data.name_ar,
      account_type: data.account_type as AccountType,
      is_header: data.is_header,
      parent: data.parent || undefined,
      description: data.description,
      description_ar: data.description_ar,
    });
  };

  // Filter accounts that can be parents (headers only)
  const parentAccounts = accounts?.filter((a) => a.is_header) || [];

  return (
    <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-6">
      {/* Code */}
      <div className="space-y-2">
        <Label htmlFor="code">{t("accounting:account.code")} *</Label>
        <Input
          id="code"
          {...form.register("code")}
          placeholder="1000"
          className="font-mono ltr-code"
        />
        {form.formState.errors.code && (
          <p className="text-sm text-destructive">
            {form.formState.errors.code.message}
          </p>
        )}
      </div>

      {/* Name (English) */}
      <div className="space-y-2">
        <Label htmlFor="name">{t("accounting:account.name")} *</Label>
        <Input
          id="name"
          {...form.register("name")}
          placeholder="Cash"
        />
        {form.formState.errors.name && (
          <p className="text-sm text-destructive">
            {form.formState.errors.name.message}
          </p>
        )}
      </div>

      {/* Name (Arabic) */}
      <div className="space-y-2">
        <Label htmlFor="name_ar">{t("accounting:account.nameAr")}</Label>
        <Input
          id="name_ar"
          {...form.register("name_ar")}
          placeholder="النقدية"
          dir="rtl"
        />
      </div>

      {/* Account Type */}
      <div className="space-y-2">
        <Label htmlFor="account_type">{t("accounting:account.type")} *</Label>
        <Select
          value={form.watch("account_type")}
          onValueChange={(value) => form.setValue("account_type", value)}
        >
          <SelectTrigger>
            <SelectValue placeholder={t("accounting:account.type")} />
          </SelectTrigger>
          <SelectContent>
            {ACCOUNT_TYPES.map((type) => (
              <SelectItem key={type} value={type}>
                {t(`accounting:accountTypes.${type}`, type)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {form.formState.errors.account_type && (
          <p className="text-sm text-destructive">
            {form.formState.errors.account_type.message}
          </p>
        )}
      </div>

      {/* Parent Account */}
      <div className="space-y-2">
        <Label htmlFor="parent">{t("accounting:account.parent")}</Label>
        <Select
          value={form.watch("parent") || "__none__"}
          onValueChange={(value) => form.setValue("parent", value === "__none__" ? "" : value)}
        >
          <SelectTrigger>
            <SelectValue placeholder="None" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__">None</SelectItem>
            {parentAccounts.map((account) => (
              <SelectItem key={account.code} value={account.code}>
                {account.code} - {account.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Is Header */}
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="is_header"
          {...form.register("is_header")}
          className="h-4 w-4 rounded border-input"
        />
        <Label htmlFor="is_header" className="font-normal">
          {t("accounting:account.isHeader")}
        </Label>
      </div>

      {/* Description */}
      <div className="space-y-2">
        <Label htmlFor="description">{t("accounting:account.description")}</Label>
        <textarea
          id="description"
          {...form.register("description")}
          className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        />
      </div>

      {/* Description (Arabic) */}
      <div className="space-y-2">
        <Label htmlFor="description_ar">{t("accounting:account.descriptionAr")}</Label>
        <textarea
          id="description_ar"
          {...form.register("description_ar")}
          dir="rtl"
          className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        />
      </div>

      {/* Extra content (e.g., analysis dimensions) */}
      {extraContent}

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
