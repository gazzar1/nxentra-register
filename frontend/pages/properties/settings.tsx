import { useEffect, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { AppLayout } from "@/components/layout";
import { PageHeader, LoadingSpinner } from "@/components/common";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import { useAccounts } from "@/queries/useAccounts";
import {
  usePropertyAccountMapping,
  useUpdatePropertyAccountMapping,
} from "@/queries/useProperties";
import type { Account } from "@/types/account";
import type { PropertyAccountMapping } from "@/types/properties";
import { Save } from "lucide-react";

const NONE_VALUE = "__none__";

interface AccountFieldConfig {
  key: keyof Pick<
    PropertyAccountMapping,
    | "rental_income_account"
    | "other_income_account"
    | "accounts_receivable_account"
    | "cash_bank_account"
    | "unapplied_cash_account"
    | "security_deposit_account"
    | "accounts_payable_account"
    | "property_expense_account"
  >;
  label: string;
  description: string;
  accountTypes?: string[];
}

const ACCOUNT_SECTIONS: {
  title: string;
  description: string;
  fields: AccountFieldConfig[];
}[] = [
  {
    title: "Revenue Accounts",
    description: "Accounts used for recognizing rental and other property income.",
    fields: [
      {
        key: "rental_income_account",
        label: "Rental Income",
        description: "Revenue account for rent invoices.",
        accountTypes: ["REVENUE"],
      },
      {
        key: "other_income_account",
        label: "Other Income",
        description: "Revenue account for late fees, penalties, etc.",
        accountTypes: ["REVENUE"],
      },
    ],
  },
  {
    title: "Asset Accounts",
    description: "Accounts for receivables and cash management.",
    fields: [
      {
        key: "accounts_receivable_account",
        label: "Accounts Receivable",
        description: "Control account for tenant balances owed.",
        accountTypes: ["ASSET"],
      },
      {
        key: "cash_bank_account",
        label: "Cash / Bank",
        description: "Default bank account for deposit and payment entries.",
        accountTypes: ["ASSET"],
      },
      {
        key: "unapplied_cash_account",
        label: "Unapplied Cash",
        description: "Holding account for payments not yet allocated to invoices.",
        accountTypes: ["ASSET"],
      },
    ],
  },
  {
    title: "Liability Accounts",
    description: "Accounts for deposits held and amounts owed.",
    fields: [
      {
        key: "security_deposit_account",
        label: "Security Deposit",
        description: "Liability account for tenant security deposits held.",
        accountTypes: ["LIABILITY"],
      },
      {
        key: "accounts_payable_account",
        label: "Accounts Payable",
        description: "Payable account for property-related vendor bills.",
        accountTypes: ["LIABILITY"],
      },
    ],
  },
  {
    title: "Expense Accounts",
    description: "Accounts for property operating costs.",
    fields: [
      {
        key: "property_expense_account",
        label: "Property Expense",
        description: "Default expense account for maintenance, repairs, etc.",
        accountTypes: ["EXPENSE"],
      },
    ],
  },
];

function AccountSelect({
  accounts,
  value,
  onChange,
  placeholder,
  accountTypes,
}: {
  accounts: Account[];
  value: number | null;
  onChange: (value: number | null) => void;
  placeholder?: string;
  accountTypes?: string[];
}) {
  const filtered = accounts.filter((a) => {
    if (a.is_header) return false;
    if (accountTypes && accountTypes.length > 0) {
      return accountTypes.includes(a.account_type);
    }
    return true;
  });

  return (
    <Select
      value={value ? String(value) : NONE_VALUE}
      onValueChange={(v) => onChange(v === NONE_VALUE ? null : Number(v))}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder || "Select account..."} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE_VALUE}>— None —</SelectItem>
        {filtered.map((account) => (
          <SelectItem key={account.id} value={String(account.id)}>
            {account.code} — {account.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export default function PropertySettingsPage() {
  const { t } = useTranslation("common");
  const { toast } = useToast();
  const { data: mapping, isLoading: mappingLoading } = usePropertyAccountMapping();
  const { data: accounts, isLoading: accountsLoading } = useAccounts();
  const updateMapping = useUpdatePropertyAccountMapping();

  const [form, setForm] = useState<Record<string, number | null>>({});
  const [isDirty, setIsDirty] = useState(false);

  const isLoading = mappingLoading || accountsLoading;

  // Initialize form from mapping data
  useEffect(() => {
    if (mapping && typeof mapping === "object" && "id" in mapping) {
      const m = mapping as PropertyAccountMapping;
      setForm({
        rental_income_account_id: m.rental_income_account,
        other_income_account_id: m.other_income_account,
        accounts_receivable_account_id: m.accounts_receivable_account,
        cash_bank_account_id: m.cash_bank_account,
        unapplied_cash_account_id: m.unapplied_cash_account,
        security_deposit_account_id: m.security_deposit_account,
        accounts_payable_account_id: m.accounts_payable_account,
        property_expense_account_id: m.property_expense_account,
      });
    }
  }, [mapping]);

  const handleChange = (fieldKey: string, value: number | null) => {
    setForm((prev) => ({ ...prev, [`${fieldKey}_id`]: value }));
    setIsDirty(true);
  };

  const handleSave = async () => {
    try {
      await updateMapping.mutateAsync(form);
      setIsDirty(false);
      toast({
        title: t("messages.success"),
        description: "Account mapping saved successfully.",
        variant: "success",
      });
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const accountList = Array.isArray(accounts) ? accounts : [];

  return (
    <AppLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <PageHeader
            title="Property Settings"
            subtitle="Configure which GL accounts are used for property accounting entries. These must be set before activating leases or recording payments."
          />
          <Button
            onClick={handleSave}
            disabled={!isDirty || updateMapping.isPending}
          >
            <Save className="me-2 h-4 w-4" />
            {updateMapping.isPending ? t("actions.loading") : "Save Mapping"}
          </Button>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : (
          <div className="space-y-6 max-w-2xl">
            {ACCOUNT_SECTIONS.map((section) => (
              <Card key={section.title}>
                <CardHeader>
                  <CardTitle className="text-base">{section.title}</CardTitle>
                  <CardDescription>{section.description}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                  {section.fields.map((field) => (
                    <div key={field.key} className="space-y-1.5">
                      <Label>{field.label}</Label>
                      <AccountSelect
                        accounts={accountList}
                        value={form[`${field.key}_id`] ?? null}
                        onChange={(v) => handleChange(field.key, v)}
                        accountTypes={field.accountTypes}
                      />
                      <p className="text-xs text-muted-foreground">
                        {field.description}
                      </p>
                    </div>
                  ))}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
