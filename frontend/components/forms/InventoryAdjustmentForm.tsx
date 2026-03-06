// components/forms/InventoryAdjustmentForm.tsx
// Form component for creating inventory adjustments

import { useRef } from "react";
import { useForm, useFieldArray } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "next-i18next";
import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useFormKeyboardShortcuts } from "@/lib/useFormKeyboardShortcuts";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent } from "@/components/ui/card";
import { InventoryAdjustmentPayload } from "@/types/inventory";
import { useItems } from "@/queries/useSales";
import { useAccounts } from "@/queries/useAccounts";
import { useWarehouses } from "@/queries/useInventory";

const lineSchema = z.object({
  item_id: z.number().min(1, "Item is required"),
  warehouse_id: z.number().nullable().optional(),
  qty_delta: z.number().refine((v) => v !== 0, "Quantity cannot be zero"),
  unit_cost: z.number().nullable().optional(),
});

const adjustmentSchema = z.object({
  adjustment_date: z.string().min(1, "Date is required"),
  reason: z.string().min(1, "Reason is required").max(500),
  adjustment_account_id: z.number().min(1, "Adjustment account is required"),
  lines: z.array(lineSchema).min(1, "At least one line is required"),
});

type AdjustmentFormData = z.infer<typeof adjustmentSchema>;

interface InventoryAdjustmentFormProps {
  onSubmit: (data: InventoryAdjustmentPayload) => void;
  isSubmitting?: boolean;
}

export function InventoryAdjustmentForm({
  onSubmit,
  isSubmitting = false,
}: InventoryAdjustmentFormProps) {
  const { t } = useTranslation(["common", "inventory"]);
  const formRef = useRef<HTMLFormElement>(null);

  const { data: items } = useItems({ item_type: "INVENTORY", is_active: true });
  const { data: accounts } = useAccounts({ type: "EXPENSE" });
  const { data: warehouses } = useWarehouses({ is_active: true });

  const {
    register,
    handleSubmit,
    control,
    formState: { errors },
    setValue,
    watch,
  } = useForm<AdjustmentFormData>({
    resolver: zodResolver(adjustmentSchema),
    defaultValues: {
      adjustment_date: new Date().toISOString().split("T")[0],
      reason: "",
      adjustment_account_id: 0,
      lines: [{ item_id: 0, warehouse_id: null, qty_delta: 0, unit_cost: null }],
    },
  });

  const { fields, append, remove } = useFieldArray({
    control,
    name: "lines",
  });

  const handleFormSubmit = (data: AdjustmentFormData) => {
    onSubmit({
      adjustment_date: data.adjustment_date,
      reason: data.reason,
      adjustment_account_id: data.adjustment_account_id,
      lines: data.lines.map((line) => ({
        item_id: line.item_id,
        warehouse_id: line.warehouse_id || null,
        qty_delta: line.qty_delta,
        unit_cost: line.unit_cost || null,
      })),
    });
  };

  const addLine = () => {
    append({ item_id: 0, warehouse_id: null, qty_delta: 0, unit_cost: null });
  };

  useFormKeyboardShortcuts({
    formRef,
    onSave: () => handleSubmit(handleFormSubmit)(),
    onSubmit: () => handleSubmit(handleFormSubmit)(),
    enabled: !isSubmitting,
  });

  return (
    <form ref={formRef} onSubmit={handleSubmit(handleFormSubmit)} className="space-y-6">
      {/* Header Fields */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Date */}
        <div className="space-y-2">
          <Label htmlFor="adjustment_date">{t("inventory:adjustment.date")} *</Label>
          <Input
            id="adjustment_date"
            type="date"
            {...register("adjustment_date")}
          />
          {errors.adjustment_date && (
            <p className="text-sm text-destructive">{errors.adjustment_date.message}</p>
          )}
        </div>

        {/* Adjustment Account */}
        <div className="space-y-2">
          <Label htmlFor="adjustment_account_id">
            {t("inventory:adjustment.adjustmentAccount")} *
          </Label>
          <Select
            value={watch("adjustment_account_id")?.toString() || ""}
            onValueChange={(value) => setValue("adjustment_account_id", parseInt(value))}
          >
            <SelectTrigger>
              <SelectValue placeholder={t("inventory:adjustment.selectAccount")} />
            </SelectTrigger>
            <SelectContent>
              {accounts?.map((account) => (
                <SelectItem key={account.id} value={account.id.toString()}>
                  {account.code} - {account.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errors.adjustment_account_id && (
            <p className="text-sm text-destructive">
              {errors.adjustment_account_id.message}
            </p>
          )}
        </div>
      </div>

      {/* Reason */}
      <div className="space-y-2">
        <Label htmlFor="reason">{t("inventory:adjustment.reason")} *</Label>
        <Textarea
          id="reason"
          {...register("reason")}
          placeholder={t("inventory:adjustment.reasonPlaceholder")}
          rows={2}
        />
        {errors.reason && (
          <p className="text-sm text-destructive">{errors.reason.message}</p>
        )}
      </div>

      {/* Lines */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <Label>{t("inventory:adjustment.lines")}</Label>
          <Button type="button" variant="outline" size="sm" onClick={addLine}>
            <Plus className="h-4 w-4 mr-2" />
            {t("inventory:adjustment.addLine")}
          </Button>
        </div>

        {errors.lines && typeof errors.lines === "object" && "message" in errors.lines && (
          <p className="text-sm text-destructive">{errors.lines.message as string}</p>
        )}

        <div className="space-y-3">
          {fields.map((field, index) => (
            <Card key={field.id}>
              <CardContent className="pt-4">
                <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                  {/* Item */}
                  <div className="md:col-span-2 space-y-2">
                    <Label>{t("inventory:adjustment.item")}</Label>
                    <Select
                      value={watch(`lines.${index}.item_id`)?.toString() || ""}
                      onValueChange={(value) =>
                        setValue(`lines.${index}.item_id`, parseInt(value))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue placeholder={t("inventory:adjustment.selectItem")} />
                      </SelectTrigger>
                      <SelectContent>
                        {items?.map((item) => (
                          <SelectItem key={item.id} value={item.id.toString()}>
                            {item.code} - {item.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {errors.lines?.[index]?.item_id && (
                      <p className="text-sm text-destructive">
                        {errors.lines[index]?.item_id?.message}
                      </p>
                    )}
                  </div>

                  {/* Warehouse */}
                  <div className="space-y-2">
                    <Label>{t("inventory:adjustment.warehouse")}</Label>
                    <Select
                      value={watch(`lines.${index}.warehouse_id`)?.toString() || "default"}
                      onValueChange={(value) =>
                        setValue(
                          `lines.${index}.warehouse_id`,
                          value === "default" ? null : parseInt(value)
                        )
                      }
                    >
                      <SelectTrigger>
                        <SelectValue placeholder={t("inventory:adjustment.defaultWarehouse")} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="default">
                          {t("inventory:adjustment.defaultWarehouse")}
                        </SelectItem>
                        {warehouses?.map((warehouse) => (
                          <SelectItem key={warehouse.id} value={warehouse.id.toString()}>
                            {warehouse.code}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Quantity Delta */}
                  <div className="space-y-2">
                    <Label>{t("inventory:adjustment.qtyDelta")}</Label>
                    <Input
                      type="number"
                      step="0.0001"
                      {...register(`lines.${index}.qty_delta`, { valueAsNumber: true })}
                      placeholder="+/-"
                    />
                    {errors.lines?.[index]?.qty_delta && (
                      <p className="text-sm text-destructive">
                        {errors.lines[index]?.qty_delta?.message}
                      </p>
                    )}
                  </div>

                  {/* Unit Cost (optional) */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label>{t("inventory:adjustment.unitCost")}</Label>
                      {fields.length > 1 && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6"
                          onClick={() => remove(index)}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      )}
                    </div>
                    <Input
                      type="number"
                      step="0.01"
                      {...register(`lines.${index}.unit_cost`, {
                        setValueAs: (v) => (v === "" ? null : parseFloat(v)),
                      })}
                      placeholder={t("inventory:adjustment.autoCalc")}
                    />
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {/* Submit Button */}
      <div className="flex justify-end gap-4">
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? t("common:saving") : t("inventory:adjustment.submit")}
        </Button>
      </div>
    </form>
  );
}
