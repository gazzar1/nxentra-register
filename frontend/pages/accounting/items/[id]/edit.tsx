import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Save, Upload, X, ImageIcon, Trash2 } from "lucide-react";
import { useForm, Controller } from "react-hook-form";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader, LoadingSpinner } from "@/components/common";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAccounts } from "@/queries/useAccounts";
import { useItem, useUpdateItem, useTaxCodes } from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { ItemType, CostingMethod } from "@/types/sales";

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
  default_cost: string;
  // External link
  external_url: string;
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

export default function EditItemPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { id } = router.query;
  const { toast } = useToast();
  const { data: item, isLoading } = useItem(parseInt(id as string));
  const { data: accounts } = useAccounts();
  const { data: taxCodes } = useTaxCodes();
  const updateItem = useUpdateItem();

  const [selectedImage, setSelectedImage] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [existingImageUrl, setExistingImageUrl] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const allowed = [".png", ".jpg", ".jpeg", ".webp"];
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!allowed.includes(ext)) {
      toast({ title: "Invalid file type", description: "Allowed: PNG, JPG, WEBP", variant: "destructive" });
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      toast({ title: "File too large", description: "Maximum 10MB", variant: "destructive" });
      return;
    }
    setSelectedImage(file);
    setImagePreview(URL.createObjectURL(file));
  };

  const handleDeleteImage = async () => {
    try {
      const { default: apiClient } = await import("@/lib/api-client");
      await apiClient.delete(`/sales/items/${id}/image/`);
      setExistingImageUrl(null);
      setSelectedImage(null);
      setImagePreview(null);
      toast({ title: "Photo removed" });
    } catch {
      toast({ title: "Error", description: "Failed to remove photo", variant: "destructive" });
    }
  };

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
    reset,
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
      default_cost: "0",
    },
  });

  const watchItemType = watch("item_type");

  // Populate form when item data loads
  useEffect(() => {
    if (item) {
      reset({
        code: item.code,
        name: item.name,
        name_ar: item.name_ar || "",
        item_type: item.item_type,
        sales_account_id: item.sales_account?.toString() || "",
        purchase_account_id: item.purchase_account?.toString() || "",
        default_unit_price: item.default_unit_price || "0",
        default_tax_code_id: item.default_tax_code?.toString() || "",
        uom: item.uom || "",
        inventory_account_id: item.inventory_account?.toString() || "",
        cogs_account_id: item.cogs_account?.toString() || "",
        costing_method: item.costing_method || "WEIGHTED_AVERAGE",
        default_cost: item.default_cost?.toString() || "0",
        external_url: item.external_url || "",
      });
      // Set existing image
      if ((item as any).image_url) {
        setExistingImageUrl((item as any).image_url);
      }
    }
  }, [item, reset]);

  const onSubmit = async (data: ItemFormData) => {
    if (!item) return;

    try {
      // Only include account fields if they have a value — sending null
      // would wipe accounts that were set by Shopify auto-import or backfill.
      const payload: Record<string, unknown> = {
        code: data.code,
        name: data.name,
        name_ar: data.name_ar || undefined,
        item_type: data.item_type,
        default_unit_price: data.default_unit_price || "0",
        default_cost: data.default_cost || "0",
        uom: data.uom || undefined,
        costing_method: data.costing_method || undefined,
      };
      if (data.sales_account_id) payload.sales_account_id = parseInt(data.sales_account_id);
      if (data.purchase_account_id) payload.purchase_account_id = parseInt(data.purchase_account_id);
      if (data.default_tax_code_id) payload.default_tax_code_id = parseInt(data.default_tax_code_id);
      if (data.inventory_account_id) payload.inventory_account_id = parseInt(data.inventory_account_id);
      if (data.cogs_account_id) payload.cogs_account_id = parseInt(data.cogs_account_id);

      await updateItem.mutateAsync({
        id: item.id,
        data: payload,
      });
      // Upload new image if selected
      if (selectedImage) {
        try {
          const formData = new FormData();
          formData.append("image", selectedImage);
          const { default: apiClient } = await import("@/lib/api-client");
          await apiClient.post(`/sales/items/${item.id}/image/`, formData, {
            headers: { "Content-Type": "multipart/form-data" },
          });
        } catch {
          toast({ title: "Item updated", description: "Saved but photo upload failed.", variant: "default" });
          router.push("/accounting/items");
          return;
        }
      }

      toast({
        title: "Item updated",
        description: `${data.name} has been updated successfully.`,
      });
      router.push("/accounting/items");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.error || "Failed to update item.",
        variant: "destructive",
      });
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <LoadingSpinner />
      </AppLayout>
    );
  }

  if (!item) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <p className="text-muted-foreground">Item not found</p>
          <Link href="/accounting/items">
            <Button variant="link">Back to items</Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <PageHeader
          title="Edit Item"
          subtitle={`Editing ${item.name}`}
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
                {isSubmitting ? "Saving..." : "Save Changes"}
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

        {/* Item Photo */}
        <Card>
          <CardHeader>
            <CardTitle>Item Photo</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-start gap-6">
              {(imagePreview || existingImageUrl) ? (
                <div className="relative">
                  <img
                    src={imagePreview || existingImageUrl || ""}
                    alt="Item"
                    className="h-32 w-32 rounded-lg object-cover border"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      if (imagePreview) {
                        setSelectedImage(null);
                        setImagePreview(null);
                        if (fileInputRef.current) fileInputRef.current.value = "";
                      } else {
                        handleDeleteImage();
                      }
                    }}
                    className="absolute -top-2 -end-2 rounded-full bg-destructive p-1 text-destructive-foreground shadow-sm hover:bg-destructive/90"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ) : (
                <div className="flex h-32 w-32 items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/25">
                  <ImageIcon className="h-8 w-8 text-muted-foreground/50" />
                </div>
              )}
              <div className="space-y-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".png,.jpg,.jpeg,.webp"
                  onChange={handleImageSelect}
                  className="hidden"
                />
                <Button type="button" variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
                  <Upload className="h-4 w-4 me-2" />
                  {(selectedImage || existingImageUrl) ? "Change Photo" : "Upload Photo"}
                </Button>
                <p className="text-xs text-muted-foreground">PNG, JPG, or WEBP. Max 10MB.</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* External Link */}
        <Card>
          <CardHeader>
            <CardTitle>External Link</CardTitle>
          </CardHeader>
          <CardContent>
            <div>
              <Label htmlFor="external_url">Product Page URL</Label>
              <Input
                id="external_url"
                type="url"
                placeholder="https://instagram.com/p/... or product page URL"
                {...register("external_url")}
              />
              <p className="text-xs text-muted-foreground mt-1">
                Link to Instagram, website, or external catalog
              </p>
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

              {/* Default cost - editable, used for COGS when no purchase history */}
              <div className="space-y-2">
                <Label htmlFor="default_cost">Default Cost</Label>
                <Input
                  {...register("default_cost")}
                  type="number"
                  step="0.01"
                  min="0"
                  placeholder="0.00"
                />
                <p className="text-sm text-muted-foreground">
                  Used for COGS when no purchase history exists
                </p>
              </div>

              {/* Read-only cost values */}
              <div className="space-y-2">
                <Label>Average Cost</Label>
                <Input
                  value={item?.average_cost || "0.00"}
                  readOnly
                  disabled
                  className="bg-muted"
                />
                <p className="text-sm text-muted-foreground">
                  Calculated from stock ledger
                </p>
              </div>

              <div className="space-y-2">
                <Label>Last Cost</Label>
                <Input
                  value={item?.last_cost || "0.00"}
                  readOnly
                  disabled
                  className="bg-muted"
                />
                <p className="text-sm text-muted-foreground">
                  Cost from last purchase
                </p>
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
