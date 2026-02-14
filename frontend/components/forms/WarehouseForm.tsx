// components/forms/WarehouseForm.tsx
// Form component for creating and editing warehouses

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "next-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { Warehouse, WarehouseCreatePayload } from "@/types/inventory";

const warehouseSchema = z.object({
  code: z.string().min(1, "Code is required").max(20, "Code must be 20 characters or less"),
  name: z.string().min(1, "Name is required").max(255),
  name_ar: z.string().max(255).optional(),
  address: z.string().optional(),
  is_default: z.boolean().optional(),
  is_active: z.boolean().optional(),
});

type WarehouseFormData = z.infer<typeof warehouseSchema>;

interface WarehouseFormProps {
  initialData?: Warehouse;
  onSubmit: (data: WarehouseCreatePayload) => void;
  isSubmitting?: boolean;
  isEdit?: boolean;
}

export function WarehouseForm({
  initialData,
  onSubmit,
  isSubmitting = false,
  isEdit = false,
}: WarehouseFormProps) {
  const { t } = useTranslation(["common", "inventory"]);

  const {
    register,
    handleSubmit,
    formState: { errors },
    watch,
    setValue,
  } = useForm<WarehouseFormData>({
    resolver: zodResolver(warehouseSchema),
    defaultValues: {
      code: initialData?.code || "",
      name: initialData?.name || "",
      name_ar: initialData?.name_ar || "",
      address: initialData?.address || "",
      is_default: initialData?.is_default || false,
      is_active: initialData?.is_active ?? true,
    },
  });

  const handleFormSubmit = (data: WarehouseFormData) => {
    onSubmit({
      code: data.code,
      name: data.name,
      name_ar: data.name_ar,
      address: data.address,
      is_default: data.is_default,
    });
  };

  return (
    <form onSubmit={handleSubmit(handleFormSubmit)} className="space-y-6" autoComplete="off">
      {/* Code */}
      <div className="space-y-2">
        <Label htmlFor="code">{t("common:code")} *</Label>
        <Input
          id="code"
          {...register("code")}
          disabled={isEdit}
          placeholder="e.g., MAIN, WH-01"
          className="ltr-code"
          autoComplete="off"
        />
        {errors.code && (
          <p className="text-sm text-destructive">{errors.code.message}</p>
        )}
      </div>

      {/* Name (English) */}
      <div className="space-y-2">
        <Label htmlFor="name">{t("common:name")} *</Label>
        <Input
          id="name"
          {...register("name")}
          placeholder="Warehouse name"
          autoComplete="off"
        />
        {errors.name && (
          <p className="text-sm text-destructive">{errors.name.message}</p>
        )}
      </div>

      {/* Name (Arabic) */}
      <div className="space-y-2">
        <Label htmlFor="name_ar">{t("common:nameAr")}</Label>
        <Input
          id="name_ar"
          {...register("name_ar")}
          placeholder="اسم المستودع"
          dir="rtl"
          autoComplete="off"
        />
      </div>

      {/* Address */}
      <div className="space-y-2">
        <Label htmlFor="address">{t("common:address")}</Label>
        <Textarea
          id="address"
          {...register("address")}
          placeholder="Physical address of the warehouse"
          rows={3}
          autoComplete="off"
        />
      </div>

      {/* Is Default */}
      <div className="flex items-center space-x-2 rtl:space-x-reverse">
        <Checkbox
          id="is_default"
          checked={watch("is_default")}
          onCheckedChange={(checked) => setValue("is_default", checked === true)}
        />
        <Label htmlFor="is_default" className="cursor-pointer">
          {t("inventory:warehouse.isDefault")}
        </Label>
      </div>

      {/* Is Active (only show on edit) */}
      {isEdit && (
        <div className="flex items-center space-x-2 rtl:space-x-reverse">
          <Checkbox
            id="is_active"
            checked={watch("is_active")}
            onCheckedChange={(checked) => setValue("is_active", checked === true)}
          />
          <Label htmlFor="is_active" className="cursor-pointer">
            {t("common:active")}
          </Label>
        </div>
      )}

      {/* Submit Button */}
      <div className="flex justify-end gap-4">
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? t("common:saving") : isEdit ? t("common:save") : t("common:create")}
        </Button>
      </div>
    </form>
  );
}
