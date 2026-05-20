import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Plus, Trash2, Save } from "lucide-react";
import { useForm, useFieldArray, Controller } from "react-hook-form";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { CompanyDateInput } from "@/components/ui/CompanyDateInput";
import { PageHeader } from "@/components/common";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/AuthContext";
import { useItems } from "@/queries/useSales";
import { useWarehouses, useCreateInventoryTransfer } from "@/queries/useInventory";
import { LineAvailabilityHint } from "@/components/inventory/LineAvailabilityHint";

interface TransferLineFormData {
  item_id: string;
  qty: string;
}

interface TransferFormData {
  transfer_date: string;
  source_warehouse_id: string;
  destination_warehouse_id: string;
  notes: string;
  lines: TransferLineFormData[];
}

export default function NewInventoryTransferPage() {
  const router = useRouter();
  const { toast } = useToast();
  const { company } = useAuth();
  const { data: items } = useItems();
  const { data: warehousesRes } = useWarehouses({ is_active: true });
  const warehouses = warehousesRes?.results || [];
  const createTransfer = useCreateInventoryTransfer();

  const {
    register,
    control,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<TransferFormData>({
    defaultValues: {
      transfer_date: new Date().toISOString().split("T")[0],
      source_warehouse_id: "",
      destination_warehouse_id: "",
      notes: "",
      lines: [{ item_id: "", qty: "1" }],
    },
  });

  const { fields, append, remove } = useFieldArray({ control, name: "lines" });
  const watchLines = watch("lines");
  const sourceWarehouseId = watch("source_warehouse_id");
  const destWarehouseId = watch("destination_warehouse_id");

  const onSubmit = async (data: TransferFormData) => {
    if (data.source_warehouse_id === data.destination_warehouse_id) {
      toast({
        title: "Invalid transfer",
        description: "Source and destination must be different warehouses.",
        variant: "destructive",
      });
      return;
    }
    try {
      const payload = {
        source_warehouse_id: parseInt(data.source_warehouse_id),
        destination_warehouse_id: parseInt(data.destination_warehouse_id),
        transfer_date: data.transfer_date,
        notes: data.notes,
        lines: data.lines
          .filter((l) => l.item_id && parseFloat(l.qty) > 0)
          .map((l) => ({ item_id: parseInt(l.item_id), qty: l.qty })),
      };
      if (payload.lines.length === 0) {
        toast({ title: "Add at least one line", variant: "destructive" });
        return;
      }
      const result = await createTransfer.mutateAsync(payload);
      toast({ title: "Transfer created", description: "Draft saved. Review and post." });
      const newId = (result as any)?.data?.id;
      if (newId) router.push(`/inventory/transfers/${newId}`);
      else router.push("/inventory/transfers");
    } catch (error: any) {
      const body = error?.response?.data;
      let description = "Failed to create transfer.";
      if (body?.detail) description = body.detail;
      toast({ title: "Error", description, variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <PageHeader
          title="New Inventory Transfer"
          subtitle="Move stock from one warehouse to another"
          actions={
            <div className="flex gap-2">
              <Link href="/inventory/transfers">
                <Button type="button" variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />Cancel
                </Button>
              </Link>
              <Button type="submit" disabled={isSubmitting}>
                <Save className="h-4 w-4 me-2" />
                {isSubmitting ? "Saving..." : "Save Draft"}
              </Button>
            </div>
          }
        />

        <Card>
          <CardHeader><CardTitle>Transfer Details</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label>Date *</Label>
              <CompanyDateInput
                value={watch("transfer_date")}
                onChange={(iso) => setValue("transfer_date", iso, { shouldValidate: true })}
                dateFormat={(company?.date_format as any) || "YYYY-MM-DD"}
              />
            </div>
            <div className="space-y-2">
              <Label>Source Warehouse *</Label>
              <Controller
                name="source_warehouse_id"
                control={control}
                rules={{ required: "Source required" }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger><SelectValue placeholder="From..." /></SelectTrigger>
                    <SelectContent>
                      {warehouses.map((w: any) => (
                        <SelectItem key={w.id} value={w.id.toString()}>
                          {w.code} - {w.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.source_warehouse_id && (
                <p className="text-xs text-destructive">{errors.source_warehouse_id.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label>Destination Warehouse *</Label>
              <Controller
                name="destination_warehouse_id"
                control={control}
                rules={{ required: "Destination required" }}
                render={({ field }) => (
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger><SelectValue placeholder="To..." /></SelectTrigger>
                    <SelectContent>
                      {warehouses
                        .filter((w: any) => w.id.toString() !== sourceWarehouseId)
                        .map((w: any) => (
                          <SelectItem key={w.id} value={w.id.toString()}>
                            {w.code} - {w.name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                )}
              />
              {errors.destination_warehouse_id && (
                <p className="text-xs text-destructive">{errors.destination_warehouse_id.message}</p>
              )}
            </div>
            <div className="space-y-2 md:col-span-3">
              <Label>Notes</Label>
              <Textarea {...register("notes")} placeholder="Optional notes..." rows={2} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Items</CardTitle>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => append({ item_id: "", qty: "1" })}
            >
              <Plus className="h-4 w-4 me-2" />Add Line
            </Button>
          </CardHeader>
          <CardContent>
            <table className="w-full">
              <thead>
                <tr className="border-b text-sm text-muted-foreground">
                  <th className="text-start py-2 px-2 w-[60px]">#</th>
                  <th className="text-start py-2 px-2">Item *</th>
                  <th className="text-end py-2 px-2 w-[160px]">Quantity *</th>
                  <th className="w-[40px]"></th>
                </tr>
              </thead>
              <tbody>
                {fields.map((field, index) => (
                  <tr key={field.id} className="border-b">
                    <td className="py-2 px-2 text-muted-foreground">{index + 1}</td>
                    <td className="py-2 px-2">
                      <Controller
                        name={`lines.${index}.item_id`}
                        control={control}
                        render={({ field: f }) => (
                          <Select onValueChange={f.onChange} value={f.value}>
                            <SelectTrigger className="h-8 text-xs">
                              <SelectValue placeholder="Select item" />
                            </SelectTrigger>
                            <SelectContent>
                              {items
                                ?.filter((it) => it.item_type === "INVENTORY")
                                .map((it) => (
                                  <SelectItem key={it.id} value={it.id.toString()}>
                                    {it.code} - {it.name}
                                  </SelectItem>
                                ))}
                            </SelectContent>
                          </Select>
                        )}
                      />
                    </td>
                    <td className="py-2 px-2">
                      <Input
                        {...register(`lines.${index}.qty`)}
                        type="number"
                        step="0.0001"
                        min="0"
                        className="h-8 text-xs text-end"
                      />
                      <LineAvailabilityHint
                        itemId={
                          watchLines[index]?.item_id ? parseInt(watchLines[index].item_id) : null
                        }
                        warehouseId={sourceWarehouseId ? parseInt(sourceWarehouseId) : null}
                        qty={parseFloat(watchLines[index]?.qty || "0") || 0}
                      />
                    </td>
                    <td className="py-2 px-2">
                      {fields.length > 1 && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => remove(index)}
                          className="h-8 w-8 p-0"
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!!destWarehouseId && sourceWarehouseId === destWarehouseId && (
              <p className="text-sm text-destructive mt-3">
                Source and destination must be different.
              </p>
            )}
          </CardContent>
        </Card>
      </form>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
