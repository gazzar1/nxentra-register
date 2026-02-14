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
import type { Account, AccountCreatePayload, AccountType, AccountRole, LedgerDomain } from "@/types/account";

const accountSchema = z.object({
  code: z.string().min(1, "Account code is required").max(20),
  name: z.string().min(1, "Account name is required").max(255),
  name_ar: z.string().max(255).optional(),
  account_type: z.string().min(1, "Account type is required"),
  ledger_domain: z.string().min(1, "Ledger domain is required"),
  role: z.string().optional(),
  is_header: z.boolean(),
  parent: z.string().optional(),
  description: z.string().max(1000).optional(),
  description_ar: z.string().max(1000).optional(),
  unit_of_measure: z.string().max(50).optional(),
});

type AccountFormData = z.infer<typeof accountSchema>;

interface AccountFormProps {
  initialData?: Partial<Account>;
  onSubmit: (data: AccountCreatePayload) => Promise<void>;
  isSubmitting?: boolean;
  onCancel?: () => void;
  extraContent?: React.ReactNode;
}

// Base account types (5-type system)
const ACCOUNT_TYPES: AccountType[] = [
  "ASSET",
  "LIABILITY",
  "EQUITY",
  "REVENUE",
  "EXPENSE",
];

// Ledger domains
const LEDGER_DOMAINS: { value: LedgerDomain; label: string }[] = [
  { value: "FINANCIAL", label: "Financial (Balance Sheet/P&L)" },
  { value: "STATISTICAL", label: "Statistical (Quantities/Metrics)" },
  { value: "OFF_BALANCE", label: "Off-Balance (Memorandum)" },
];

// Roles organized by account type and ledger domain
const ROLES_BY_TYPE: Record<string, { value: AccountRole; label: string }[]> = {
  // Financial roles
  "ASSET:FINANCIAL": [
    { value: "ASSET_GENERAL", label: "General Asset" },
    { value: "LIQUIDITY", label: "Cash & Bank" },
    { value: "RECEIVABLE_CONTROL", label: "Accounts Receivable (AR Control)" },
    { value: "INVENTORY_VALUE", label: "Inventory" },
    { value: "PREPAID", label: "Prepaid Expenses" },
    { value: "FIXED_ASSET_COST", label: "Fixed Assets" },
    { value: "ACCUM_DEPRECIATION", label: "Accumulated Depreciation (Contra)" },
    { value: "OTHER_ASSET", label: "Other Assets" },
  ],
  "LIABILITY:FINANCIAL": [
    { value: "LIABILITY_GENERAL", label: "General Liability" },
    { value: "PAYABLE_CONTROL", label: "Accounts Payable (AP Control)" },
    { value: "ACCRUED_EXPENSE", label: "Accrued Expenses" },
    { value: "DEFERRED_REVENUE", label: "Deferred Revenue" },
    { value: "TAX_PAYABLE", label: "Taxes Payable" },
    { value: "LOAN", label: "Loans & Borrowings" },
    { value: "OTHER_LIABILITY", label: "Other Liabilities" },
  ],
  "EQUITY:FINANCIAL": [
    { value: "CAPITAL", label: "Capital / Share Capital" },
    { value: "RETAINED_EARNINGS", label: "Retained Earnings" },
    { value: "CURRENT_YEAR_EARNINGS", label: "Current Year Earnings" },
    { value: "DRAWINGS", label: "Drawings (Contra)" },
    { value: "RESERVE", label: "Reserves" },
    { value: "OTHER_EQUITY", label: "Other Equity" },
  ],
  "REVENUE:FINANCIAL": [
    { value: "SALES", label: "Sales Revenue" },
    { value: "SERVICE", label: "Service Revenue" },
    { value: "OTHER_INCOME", label: "Other Income" },
    { value: "FINANCIAL_INCOME", label: "Financial Income (Interest)" },
    { value: "CONTRA_REVENUE", label: "Sales Returns (Contra)" },
  ],
  "EXPENSE:FINANCIAL": [
    { value: "COGS", label: "Cost of Goods Sold" },
    { value: "OPERATING_EXPENSE", label: "Operating Expenses" },
    { value: "ADMIN_EXPENSE", label: "Administrative Expenses" },
    { value: "FINANCIAL_EXPENSE", label: "Financial Expenses (Interest)" },
    { value: "DEPRECIATION_EXPENSE", label: "Depreciation Expense" },
    { value: "TAX_EXPENSE", label: "Tax Expense" },
    { value: "OTHER_EXPENSE", label: "Other Expenses" },
  ],
  // Statistical roles (any account type)
  "STATISTICAL": [
    { value: "STAT_GENERAL", label: "General Statistical" },
    { value: "STAT_INVENTORY_QTY", label: "Inventory Quantities" },
    { value: "STAT_PRODUCTION_QTY", label: "Production Quantities" },
  ],
  // Off-balance roles (any account type)
  "OFF_BALANCE": [
    { value: "OBS_GENERAL", label: "General Off-Balance" },
    { value: "OBS_CONTINGENT", label: "Contingent Liabilities/Assets" },
  ],
};

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
      ledger_domain: initialData?.ledger_domain || "FINANCIAL",
      role: initialData?.role || "",
      is_header: initialData?.is_header || false,
      parent: initialData?.parent_code || "",
      description: initialData?.description || "",
      description_ar: initialData?.description_ar || "",
      unit_of_measure: initialData?.unit_of_measure || "",
    },
  });

  // Watch values for dynamic role options
  const watchedType = form.watch("account_type");
  const watchedDomain = form.watch("ledger_domain");

  // Get available roles based on account type and ledger domain
  const getAvailableRoles = () => {
    if (watchedDomain === "STATISTICAL") {
      return ROLES_BY_TYPE["STATISTICAL"] || [];
    }
    if (watchedDomain === "OFF_BALANCE") {
      return ROLES_BY_TYPE["OFF_BALANCE"] || [];
    }
    // Financial domain - use type-specific roles
    const key = `${watchedType}:FINANCIAL`;
    return ROLES_BY_TYPE[key] || [];
  };

  const availableRoles = getAvailableRoles();

  const handleSubmit = async (data: AccountFormData) => {
    await onSubmit({
      code: data.code,
      name: data.name,
      name_ar: data.name_ar,
      account_type: data.account_type as AccountType,
      ledger_domain: data.ledger_domain as LedgerDomain,
      role: (data.role || undefined) as AccountRole | undefined,
      is_header: data.is_header,
      parent: data.parent || undefined,
      description: data.description,
      description_ar: data.description_ar,
      unit_of_measure: data.unit_of_measure || undefined,
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

      {/* Ledger Domain */}
      <div className="space-y-2">
        <Label htmlFor="ledger_domain">Ledger Domain *</Label>
        <Select
          value={form.watch("ledger_domain")}
          onValueChange={(value) => {
            form.setValue("ledger_domain", value);
            form.setValue("role", ""); // Reset role when domain changes
          }}
        >
          <SelectTrigger>
            <SelectValue placeholder="Select ledger domain" />
          </SelectTrigger>
          <SelectContent>
            {LEDGER_DOMAINS.map((domain) => (
              <SelectItem key={domain.value} value={domain.value}>
                {domain.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {form.formState.errors.ledger_domain && (
          <p className="text-sm text-destructive">
            {form.formState.errors.ledger_domain.message}
          </p>
        )}
      </div>

      {/* Account Type */}
      <div className="space-y-2">
        <Label htmlFor="account_type">{t("accounting:account.type")} *</Label>
        <Select
          value={form.watch("account_type")}
          onValueChange={(value) => {
            form.setValue("account_type", value);
            form.setValue("role", ""); // Reset role when type changes
          }}
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

      {/* Account Role */}
      {availableRoles.length > 0 && (
        <div className="space-y-2">
          <Label htmlFor="role">Account Role</Label>
          <Select
            value={form.watch("role") || "__none__"}
            onValueChange={(value) => form.setValue("role", value === "__none__" ? "" : value)}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select role (optional)" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">General (No specific role)</SelectItem>
              {availableRoles.map((role) => (
                <SelectItem key={role.value} value={role.value}>
                  {role.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {watchedDomain === "STATISTICAL" && "Statistical accounts track quantities and metrics without financial values."}
            {watchedDomain === "OFF_BALANCE" && "Off-balance accounts are memorandum entries not affecting financial statements."}
            {watchedDomain === "FINANCIAL" && form.watch("role") === "RECEIVABLE_CONTROL" && "AR Control accounts require customer selection on journal entries."}
            {watchedDomain === "FINANCIAL" && form.watch("role") === "PAYABLE_CONTROL" && "AP Control accounts require vendor selection on journal entries."}
          </p>
        </div>
      )}

      {/* Unit of Measure (for Statistical accounts) */}
      {watchedDomain === "STATISTICAL" && (
        <div className="space-y-2">
          <Label htmlFor="unit_of_measure">Unit of Measure</Label>
          <Input
            id="unit_of_measure"
            {...form.register("unit_of_measure")}
            placeholder="e.g., units, kg, hours"
          />
          <p className="text-xs text-muted-foreground">
            The unit used for tracking quantities (e.g., pieces, kg, hours)
          </p>
        </div>
      )}

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
