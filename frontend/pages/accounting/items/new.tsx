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
import { useCreateItem, useTaxCodes } from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { ItemCreatePayload, ItemType, CostingMethod } from "@/types/sales";

interface ItemFormData {
  code: string;
  name: string;
  name_ar: string;
  item_type: ItemType;
  sales_account_id: string;
  purchase_account_id: string;
  default_unit_price: string;
  default_tax_code_id: string;
  uom: string;
  // Inventory-specific fields
  inventory_account_id: string;
  cogs_account_id: string;
  costing_method: CostingMethod;
}

const ITEM_TYPES: { value: ItemType; label: string }[] = [
  { value: "INVENTORY", label: "Inventory (Stocked)" },
  { value: "SERVICE", label: "Service (Non-stocked)" },
  { value: "NON_STOCK", label: "Non-Stock (Purchased but not tracked)" },
];

const COSTING_METHODS: { value: CostingMethod; label: string }[] = [
  { value: "WEIGHTED_AVERAGE", label: "Weighted Average" },
  { value: "FIFO", label: "FIFO (First In, First Out)" },
  { value: "LIFO", label: "LIFO (Last In, First Out)" },
];

export default function NewItemPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: accounts } = useAccounts();
  const { data: taxCodes } = useTaxCodes();
  const createItem = useCreateItem();

  const revenueAccounts = accounts?.filter(
    (a) => a.account_type === "REVENUE" && a.is_postable && !a.is_header
  );
  const expenseAccounts = accounts?.filter(
    (a) => (a.account_type === "EXPENSE" || a.account_type === "ASSET") && a.is_postable && !a.is_header
  );
  const inventoryAccounts = accounts?.filter(
    (a) => a.account_type === "ASSET" && a.is_postable && !a.is_header
  );
  const cogsAccounts = accounts?.filter(
    (a) => a.account_type === "EXPENSE" && a.is_postable && !a.is_header
  );

  const {
    register,
    control,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<ItemFormData>({
    defaultValues: {
      code: "",
      name: "",
      name_ar: "",
      item_type: "INVENTORY",
      sales_account_id: "",
      purchase_account_id: "",
      default_unit_price: "0",
      default_tax_code_id: "",
      uom: "",
      inventory_account_id: "",
      cogs_account_id: "",
      costing_method: "WEIGHTED_AVERAGE",
    },
  });

  const watchItemType = watch("item_type");

  const onSubmit = async (data: ItemFormData) => {
    try {
      const payload: ItemCreatePayload = {
        code: data.code,
        name: data.name,
        name_ar: data.name_ar || undefined,
        item_type: data.item_type,
        sales_account_id: data.sales_account_id ? parseInt(data.sales_account_id) : null,
        purchase_account_id: data.purchase_account_id ? parseInt(data.purchase_account_id) : null,
        default_unit_price: data.default_unit_price || "0",
        default_tax_code_id: data.default_tax_code_id ? parseInt(data.default_tax_code_id) : null,
        uom: data.uom || undefined,
        // Inventory-specific fields
        inventory_account_id: data.inventory_account_id ? parseInt(data.inventory_account_id) : null,
        cogs_account_id: data.cogs_account_id ? parseInt(data.cogs_account_id) : null,
        costing_method: data.costing_method || undefined,
      };

      await createItem.mutateAsync(payload);
      toast({
        title: "Item created",
        description: `${data.name} has been created successfully.`,
      });
      router.push("/accounting/items");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.error || "Failed to create item.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <PageHeader
          title="New Item"
          subtitle="Add a new product or service to your catalog"
          actions={
            <div className="flex gap-2">
              <Link href="/accounting/items">
                <Button type="button" variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />
                  Cancel
                </Button>
              </Link>
              <Button type="submit" disabled={isSubmitting}>
                <Save className="h-4 w-4 me-2" />
                {isSubmitting ? "Saving..." : "Save Item"}
              </Button>
            </div>
          }
        />

        <Card>
          <CardHeader>
            <CardTitle>Item Details</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="code">Item Code *</Label>
              <Input
                id="code"
                {...register("code", { required: "Item code is required" })}
                placeholder="ITEM-001"
              />
              {errors.code && (
                <p className="text-sm text-destructive">{errors.code.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="item_type">Item Type *</Label>
              <Controller
                name="item_type"
                control={control}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select type" />
                    </SelectTrigger>
                    <SelectContent>
                      {ITEM_TYPES.map((type) => (
                        <SelectItem key={type.value} value={type.value}>
                          {type.label}
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
                placeholder="Item name"
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
                placeholder="اسم الصنف"
                dir="rtl"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="default_unit_price">Default Unit Price</Label>
              <Input
                id="default_unit_price"
                type="number"
                step="0.01"
                min="0"
                {...register("default_unit_price")}
                placeholder="0.00"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="uom">Unit of Measure</Label>
              <Input
                id="uom"
                {...register("uom")}
                placeholder="e.g., Each, Hour, Kg"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="sales_account_id">Sales Account</Label>
              <Controller
                name="sales_account_id"
                control={control}
                render={({ field }) => (
                  <Select
                    onValueChange={(val) => field.onChange(val === "_none" ? "" : val)}
                    value={field.value || "_none"}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select account" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="_none">None</SelectItem>
                      {revenueAccounts?.map((acc) => (
                        <SelectItem key={acc.id} value={acc.id.toString()}>
                          {acc.code} - {acc.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="purchase_account_id">Purchase Account</Label>
              <Controller
                name="purchase_account_id"
                control={control}
                render={({ field }) => (
                  <Select
                    onValueChange={(val) => field.onChange(val === "_none" ? "" : val)}
                    value={field.value || "_none"}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select account" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="_none">None</SelectItem>
                      {expenseAccounts?.map((acc) => (
                        <SelectItem key={acc.id} value={acc.id.toString()}>
                          {acc.code} - {acc.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="default_tax_code_id">Default Tax Code</Label>
              <Controller
                name="default_tax_code_id"
                control={control}
                render={({ field }) => (
                  <Select
                    onValueChange={(val) => field.onChange(val === "_none" ? "" : val)}
                    value={field.value || "_none"}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select tax code" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="_none">None</SelectItem>
                      {taxCodes?.map((tc) => (
                        <SelectItem key={tc.id} value={tc.id.toString()}>
                          {tc.code} - {tc.name} ({(parseFloat(tc.rate) * 100).toFixed(0)}%)
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
          </CardContent>
        </Card>

        {/* Inventory Settings - Only show for INVENTORY items */}
        {watchItemType === "INVENTORY" && (
          <Card>
            <CardHeader>
              <CardTitle>Inventory Settings</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="inventory_account_id">Inventory Account *</Label>
                <Controller
                  name="inventory_account_id"
                  control={control}
                  render={({ field }) => (
                    <Select
                      onValueChange={(val) => field.onChange(val === "_none" ? "" : val)}
                      value={field.value || "_none"}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select inventory account" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="_none">None</SelectItem>
                        {inventoryAccounts?.map((acc) => (
                          <SelectItem key={acc.id} value={acc.id.toString()}>
                            {acc.code} - {acc.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
                <p className="text-sm text-muted-foreground">
                  Asset account to track inventory value
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="cogs_account_id">COGS Account *</Label>
                <Controller
                  name="cogs_account_id"
                  control={control}
                  render={({ field }) => (
                    <Select
                      onValueChange={(val) => field.onChange(val === "_none" ? "" : val)}
                      value={field.value || "_none"}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select COGS account" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="_none">None</SelectItem>
                        {cogsAccounts?.map((acc) => (
                          <SelectItem key={acc.id} value={acc.id.toString()}>
                            {acc.code} - {acc.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
                <p className="text-sm text-muted-foreground">
                  Expense account for Cost of Goods Sold
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="costing_method">Costing Method</Label>
                <Controller
                  name="costing_method"
                  control={control}
                  render={({ field }) => (
                    <Select onValueChange={field.onChange} value={field.value}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select costing method" />
                      </SelectTrigger>
                      <SelectContent>
                        {COSTING_METHODS.map((method) => (
                          <SelectItem key={method.value} value={method.value}>
                            {method.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
              </div>
            </CardContent>
          </Card>
        )}
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
