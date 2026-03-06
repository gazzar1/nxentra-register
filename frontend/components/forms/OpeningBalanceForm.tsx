// components/forms/OpeningBalanceForm.tsx
// Form component for recording inventory opening balances

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent } from "@/components/ui/card";
import { OpeningBalancePayload } from "@/types/inventory";
import { useItems } from "@/queries/useSales";
import { useAccounts } from "@/queries/useAccounts";
import { useWarehouses } from "@/queries/useInventory";

const lineSchema = z.object({
  item_id: z.number().min(1, "Item is required"),
  warehouse_id: z.number().nullable().optional(),
  qty: z.number().min(0.0001, "Quantity must be positive"),
  unit_cost: z.number().min(0, "Cost must be non-negative"),
});

const openingBalanceSchema = z.object({
  as_of_date: z.string().min(1, "Date is required"),
  opening_balance_equity_account_id: z.number().min(1, "Equity account is required"),
  lines: z.array(lineSchema).min(1, "At least one line is required"),
});

type OpeningBalanceFormData = z.infer<typeof openingBalanceSchema>;

interface OpeningBalanceFormProps {
  onSubmit: (data: OpeningBalancePayload) => void;
  isSubmitting?: boolean;
}

export function OpeningBalanceForm({
  onSubmit,
  isSubmitting = false,
}: OpeningBalanceFormProps) {
  const { t } = useTranslation(["common", "inventory"]);

  const { data: items } = useItems({ item_type: "INVENTORY", is_active: true });
  const { data: accounts } = useAccounts({ type: "EQUITY" });
  const { data: warehouses } = useWarehouses({ is_active: true });

  const formRef = useRef<HTMLFormElement>(null);

  const {
    register,
    handleSubmit,
    control,
    formState: { errors },
    setValue,
    watch,
  } = useForm<OpeningBalanceFormData>({
    resolver: zodResolver(openingBalanceSchema),
    defaultValues: {
      as_of_date: new Date().toISOString().split("T")[0],
      opening_balance_equity_account_id: 0,
      lines: [{ item_id: 0, warehouse_id: null, qty: 0, unit_cost: 0 }],
    },
  });

  const { fields, append, remove } = useFieldArray({
    control,
    name: "lines",
  });

  const handleFormSubmit = (data: OpeningBalanceFormData) => {
    onSubmit({
      as_of_date: data.as_of_date,
      opening_balance_equity_account_id: data.opening_balance_equity_account_id,
      lines: data.lines.map((line) => ({
        item_id: line.item_id,
        warehouse_id: line.warehouse_id || null,
        qty: line.qty,
        unit_cost: line.unit_cost,
      })),
    });
  };

  const addLine = () => {
    append({ item_id: 0, warehouse_id: null, qty: 0, unit_cost: 0 });
  };

  useFormKeyboardShortcuts({
    formRef,
    onSave: () => handleSubmit(handleFormSubmit)(),
    onSubmit: () => handleSubmit(handleFormSubmit)(),
    enabled: !isSubmitting,
  });

  // Calculate totals
  const lines = watch("lines");
  const totalValue = lines.reduce((sum, line) => sum + (line.qty || 0) * (line.unit_cost || 0), 0);
  const totalQty = lines.reduce((sum, line) => sum + (line.qty || 0), 0);

  return (
    <form ref={formRef} onSubmit={handleSubmit(handleFormSubmit)} className="space-y-6">
      {/* Header Fields */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* As Of Date */}
        <div className="space-y-2">
          <Label htmlFor="as_of_date">{t("inventory:openingBalance.asOfDate")} *</Label>
          <Input
            id="as_of_date"
            type="date"
            {...register("as_of_date")}
          />
          {errors.as_of_date && (
            <p className="text-sm text-destructive">{errors.as_of_date.message}</p>
          )}
        </div>

        {/* Opening Balance Equity Account */}
        <div className="space-y-2">
          <Label htmlFor="opening_balance_equity_account_id">
            {t("inventory:openingBalance.equityAccount")} *
          </Label>
          <Select
            value={watch("opening_balance_equity_account_id")?.toString() || ""}
            onValueChange={(value) =>
              setValue("opening_balance_equity_account_id", parseInt(value))
            }
          >
            <SelectTrigger>
              <SelectValue placeholder={t("inventory:openingBalance.selectAccount")} />
            </SelectTrigger>
            <SelectContent>
              {accounts?.map((account) => (
                <SelectItem key={account.id} value={account.id.toString()}>
                  {account.code} - {account.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errors.opening_balance_equity_account_id && (
            <p className="text-sm text-destructive">
              {errors.opening_balance_equity_account_id.message}
            </p>
          )}
        </div>
      </div>

      {/* Lines */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <Label>{t("inventory:openingBalance.lines")}</Label>
          <Button type="button" variant="outline" size="sm" onClick={addLine}>
            <Plus className="h-4 w-4 mr-2" />
            {t("inventory:openingBalance.addLine")}
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
                    <Label>{t("inventory:openingBalance.item")}</Label>
                    <Select
                      value={watch(`lines.${index}.item_id`)?.toString() || ""}
                      onValueChange={(value) =>
                        setValue(`lines.${index}.item_id`, parseInt(value))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue placeholder={t("inventory:openingBalance.selectItem")} />
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
                    <Label>{t("inventory:openingBalance.warehouse")}</Label>
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
                        <SelectValue placeholder={t("inventory:openingBalance.defaultWarehouse")} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="default">
                          {t("inventory:openingBalance.defaultWarehouse")}
                        </SelectItem>
                        {warehouses?.map((warehouse) => (
                          <SelectItem key={warehouse.id} value={warehouse.id.toString()}>
                            {warehouse.code}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Quantity */}
                  <div className="space-y-2">
                    <Label>{t("inventory:openingBalance.qty")}</Label>
                    <Input
                      type="number"
                      step="0.0001"
                      min="0"
                      {...register(`lines.${index}.qty`, { valueAsNumber: true })}
                      placeholder="0"
                    />
                    {errors.lines?.[index]?.qty && (
                      <p className="text-sm text-destructive">
                        {errors.lines[index]?.qty?.message}
                      </p>
                    )}
                  </div>

                  {/* Unit Cost */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label>{t("inventory:openingBalance.unitCost")}</Label>
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
                      min="0"
                      {...register(`lines.${index}.unit_cost`, { valueAsNumber: true })}
                      placeholder="0.00"
                    />
                    {errors.lines?.[index]?.unit_cost && (
                      <p className="text-sm text-destructive">
                        {errors.lines[index]?.unit_cost?.message}
                      </p>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {/* Summary */}
      <Card>
        <CardContent className="py-4">
          <div className="flex justify-between text-sm">
            <span>{t("inventory:openingBalance.totalLines")}: {fields.length}</span>
            <span>{t("inventory:openingBalance.totalQty")}: {totalQty.toLocaleString()}</span>
            <span className="font-medium">
              {t("inventory:openingBalance.totalValue")}:{" "}
              {totalValue.toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </span>
          </div>
        </CardContent>
      </Card>

      {/* Submit Button */}
      <div className="flex justify-end gap-4">
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? t("common:saving") : t("inventory:openingBalance.submit")}
        </Button>
      </div>
    </form>
  );
}
