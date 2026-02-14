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
import { PageHeader } from "@/components/common";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAccounts } from "@/queries/useAccounts";
import { useCreateTaxCode } from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { TaxCodeCreatePayload, TaxDirection } from "@/types/sales";

interface TaxCodeFormData {
  code: string;
  name: string;
  name_ar: string;
  rate: string;
  direction: TaxDirection;
  tax_account_id: string;
}

const TAX_DIRECTIONS: { value: TaxDirection; label: string }[] = [
  { value: "OUTPUT", label: "Output (Sales)" },
  { value: "INPUT", label: "Input (Purchases)" },
];

export default function NewTaxCodePage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: accounts } = useAccounts();
  const createTaxCode = useCreateTaxCode();

  // Tax accounts are typically liability accounts (VAT Payable, Input VAT)
  const taxAccounts = accounts?.filter(
    (a) => (a.account_type === "LIABILITY" || a.account_type === "ASSET") && a.is_postable && !a.is_header
  );

  const {
    register,
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<TaxCodeFormData>({
    defaultValues: {
      code: "",
      name: "",
      name_ar: "",
      rate: "0.15",
      direction: "OUTPUT",
      tax_account_id: "",
    },
  });

  const onSubmit = async (data: TaxCodeFormData) => {
    try {
      const payload: TaxCodeCreatePayload = {
        code: data.code,
        name: data.name,
        name_ar: data.name_ar || undefined,
        rate: data.rate,
        direction: data.direction,
        tax_account_id: parseInt(data.tax_account_id),
      };

      await createTaxCode.mutateAsync(payload);
      toast({
        title: "Tax code created",
        description: `${data.name} has been created successfully.`,
      });
      router.push("/accounting/tax-codes");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.error || "Failed to create tax code.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <PageHeader
          title="New Tax Code"
          subtitle="Add a new tax code for sales or purchases"
          actions={
            <div className="flex gap-2">
              <Link href="/accounting/tax-codes">
                <Button type="button" variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />
                  Cancel
                </Button>
              </Link>
              <Button type="submit" disabled={isSubmitting}>
                <Save className="h-4 w-4 me-2" />
                {isSubmitting ? "Saving..." : "Save Tax Code"}
              </Button>
            </div>
          }
        />

        <Card>
          <CardHeader>
            <CardTitle>Tax Code Details</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="code">Tax Code *</Label>
              <Input
                id="code"
                {...register("code", { required: "Tax code is required" })}
                placeholder="VAT15"
              />
              {errors.code && (
                <p className="text-sm text-destructive">{errors.code.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="direction">Direction *</Label>
              <Controller
                name="direction"
                control={control}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select direction" />
                    </SelectTrigger>
                    <SelectContent>
                      {TAX_DIRECTIONS.map((dir) => (
                        <SelectItem key={dir.value} value={dir.value}>
                          {dir.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="name">Name (English) *</Label>
              <Input
                id="name"
                {...register("name", { required: "Name is required" })}
                placeholder="VAT 15%"
              />
              {errors.name && (
                <p className="text-sm text-destructive">{errors.name.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="name_ar">Name (Arabic)</Label>
              <Input
                id="name_ar"
                {...register("name_ar")}
                placeholder="ضريبة القيمة المضافة 15%"
                dir="rtl"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="rate">Tax Rate (decimal) *</Label>
              <Input
                id="rate"
                type="number"
                step="0.0001"
                min="0"
                max="1"
                {...register("rate", { required: "Rate is required" })}
                placeholder="0.15"
              />
              <p className="text-xs text-muted-foreground">
                Enter as decimal (e.g., 0.15 for 15%)
              </p>
              {errors.rate && (
                <p className="text-sm text-destructive">{errors.rate.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="tax_account_id">Tax Account *</Label>
              <Controller
                name="tax_account_id"
                control={control}
                rules={{ required: "Tax account is required" }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select account" />
                    </SelectTrigger>
                    <SelectContent>
                      {taxAccounts?.map((acc) => (
                        <SelectItem key={acc.id} value={acc.id.toString()}>
                          {acc.code} - {acc.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.tax_account_id && (
                <p className="text-sm text-destructive">{errors.tax_account_id.message}</p>
              )}
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
