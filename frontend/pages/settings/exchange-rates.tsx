import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Pencil } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { currencyOptions } from "@/lib/constants";
import {
  exchangeRatesService,
  ExchangeRate,
  ExchangeRateCreatePayload,
} from "@/services/exchange-rates.service";
import { useToast } from "@/components/ui/toaster";

export default function ExchangeRatesPage() {
  const { t } = useTranslation(["common"]);
  const { company } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingRate, setEditingRate] = useState<ExchangeRate | null>(null);
  const [form, setForm] = useState<ExchangeRateCreatePayload>({
    from_currency: "",
    to_currency: "",
    rate: "",
    effective_date: new Date().toISOString().split("T")[0],
    rate_type: "SPOT",
    source: "Manual",
  });

  const { data: rates, isLoading } = useQuery({
    queryKey: ["exchange-rates"],
    queryFn: async () => {
      const { data } = await exchangeRatesService.list();
      return data;
    },
  });

  const createMutation = useMutation({
    mutationFn: (data: ExchangeRateCreatePayload) =>
      exchangeRatesService.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["exchange-rates"] });
      setDialogOpen(false);
      resetForm();
      toast({ title: "Exchange rate saved." });
    },
    onError: () => {
      toast({ title: "Failed to save exchange rate.", variant: "destructive" });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<ExchangeRateCreatePayload> }) =>
      exchangeRatesService.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["exchange-rates"] });
      setDialogOpen(false);
      setEditingRate(null);
      resetForm();
      toast({ title: "Exchange rate updated." });
    },
    onError: () => {
      toast({ title: "Failed to update exchange rate.", variant: "destructive" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => exchangeRatesService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["exchange-rates"] });
      toast({ title: "Exchange rate deleted." });
    },
    onError: () => {
      toast({ title: "Failed to delete exchange rate.", variant: "destructive" });
    },
  });

  const resetForm = () => {
    setForm({
      from_currency: "",
      to_currency: "",
      rate: "",
      effective_date: new Date().toISOString().split("T")[0],
      rate_type: "SPOT",
      source: "Manual",
    });
  };

  const handleOpenCreate = () => {
    setEditingRate(null);
    resetForm();
    setDialogOpen(true);
  };

  const handleOpenEdit = (rate: ExchangeRate) => {
    setEditingRate(rate);
    setForm({
      from_currency: rate.from_currency,
      to_currency: rate.to_currency,
      rate: rate.rate,
      effective_date: rate.effective_date,
      rate_type: rate.rate_type,
      source: rate.source,
    });
    setDialogOpen(true);
  };

  const handleSubmit = () => {
    if (!form.from_currency || !form.to_currency || !form.rate || !form.effective_date) {
      toast({ title: "All fields are required.", variant: "destructive" });
      return;
    }
    if (editingRate) {
      updateMutation.mutate({
        id: editingRate.id,
        data: { rate: form.rate, effective_date: form.effective_date, rate_type: form.rate_type, source: form.source },
      });
    } else {
      createMutation.mutate(form);
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Exchange Rates"
          subtitle={`Manage currency exchange rates for ${company?.name ?? "your company"}`}
          actions={
            <Button onClick={handleOpenCreate}>
              <Plus className="me-2 h-4 w-4" />
              Add Rate
            </Button>
          }
        />

        <Card>
          <CardContent className="pt-6">
            {isLoading ? (
              <LoadingSpinner />
            ) : !rates || rates.length === 0 ? (
              <p className="text-center text-muted-foreground py-8">
                No exchange rates configured. Add a rate to enable multi-currency transactions.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>From</TableHead>
                    <TableHead>To</TableHead>
                    <TableHead className="text-right">Rate</TableHead>
                    <TableHead>Effective Date</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-20"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rates.map((rate) => (
                    <TableRow key={rate.id}>
                      <TableCell className="font-mono">{rate.from_currency}</TableCell>
                      <TableCell className="font-mono">{rate.to_currency}</TableCell>
                      <TableCell className="text-right tabular-nums">{rate.rate}</TableCell>
                      <TableCell>{rate.effective_date}</TableCell>
                      <TableCell>{rate.rate_type}</TableCell>
                      <TableCell className="text-muted-foreground">{rate.source}</TableCell>
                      <TableCell>
                        <div className="flex gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            onClick={() => handleOpenEdit(rate)}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-destructive"
                            onClick={() => deleteMutation.mutate(rate.id)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* Create/Edit Dialog */}
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {editingRate ? "Edit Exchange Rate" : "Add Exchange Rate"}
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <label className="text-sm font-medium">From Currency</label>
                  <Select
                    value={form.from_currency}
                    onValueChange={(v) => setForm({ ...form, from_currency: v })}
                    disabled={!!editingRate}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select..." />
                    </SelectTrigger>
                    <SelectContent>
                      {currencyOptions
                        .filter((c) => c !== form.to_currency)
                        .map((c) => (
                          <SelectItem key={c} value={c}>
                            {c}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <label className="text-sm font-medium">To Currency</label>
                  <Select
                    value={form.to_currency}
                    onValueChange={(v) => setForm({ ...form, to_currency: v })}
                    disabled={!!editingRate}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select..." />
                    </SelectTrigger>
                    <SelectContent>
                      {currencyOptions
                        .filter((c) => c !== form.from_currency)
                        .map((c) => (
                          <SelectItem key={c} value={c}>
                            {c}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium">
                  Rate (1 {form.from_currency || "FROM"} = ? {form.to_currency || "TO"})
                </label>
                <Input
                  type="number"
                  step="0.00000001"
                  min="0"
                  value={form.rate}
                  onChange={(e) => setForm({ ...form, rate: e.target.value })}
                  placeholder="e.g. 3.75"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <label className="text-sm font-medium">Effective Date</label>
                  <Input
                    type="date"
                    value={form.effective_date}
                    onChange={(e) =>
                      setForm({ ...form, effective_date: e.target.value })
                    }
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-sm font-medium">Rate Type</label>
                  <Select
                    value={form.rate_type}
                    onValueChange={(v) => setForm({ ...form, rate_type: v })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="SPOT">Spot Rate</SelectItem>
                      <SelectItem value="AVERAGE">Average Rate</SelectItem>
                      <SelectItem value="CLOSING">Closing Rate</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Source</label>
                <Input
                  value={form.source}
                  onChange={(e) => setForm({ ...form, source: e.target.value })}
                  placeholder="e.g. Manual, ECB, XE"
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDialogOpen(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleSubmit}
                disabled={createMutation.isPending || updateMutation.isPending}
              >
                {editingRate ? "Update" : "Create"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
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
