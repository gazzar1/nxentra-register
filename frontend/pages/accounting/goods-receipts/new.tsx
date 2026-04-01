import React, { useEffect, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Save, Package } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { CompanyDateInput } from "@/components/ui/CompanyDateInput";
import { PageHeader, EmptyState } from "@/components/common";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { usePurchaseOrder, useCreateGoodsReceipt } from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/AuthContext";
import { purchaseOrdersService } from "@/services/purchases.service";
import type { PurchaseOrderListItem, PurchaseOrderLine } from "@/types/purchases";

export default function NewGoodsReceiptPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { company } = useAuth();
  const createGR = useCreateGoodsReceipt();

  // PO can be pre-selected via query param
  const preSelectedPO = router.query.po ? parseInt(router.query.po as string) : null;
  const [selectedPOId, setSelectedPOId] = useState<number | null>(preSelectedPO);
  const [availablePOs, setAvailablePOs] = useState<PurchaseOrderListItem[]>([]);
  const [receiptDate, setReceiptDate] = useState(new Date().toISOString().split("T")[0]);
  const [warehouseId, setWarehouseId] = useState("");
  const [notes, setNotes] = useState("");
  const [lineQtys, setLineQtys] = useState<Record<number, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [warehouses, setWarehouses] = useState<{ id: number; code: string; name: string }[]>([]);

  const { data: selectedPO } = usePurchaseOrder(selectedPOId || 0);

  // Fetch receivable POs
  useEffect(() => {
    purchaseOrdersService.list({ status: "APPROVED", page_size: 200 }).then((res) => {
      const approved = res.data.results;
      purchaseOrdersService.list({ status: "PARTIALLY_RECEIVED", page_size: 200 }).then((res2) => {
        setAvailablePOs([...approved, ...res2.data.results]);
      });
    }).catch(() => {});
  }, []);

  // Fetch warehouses
  useEffect(() => {
    import("@/lib/api-client").then(({ default: apiClient }) => {
      apiClient.get("/inventory/warehouses/").then((res) => {
        const data = res.data.results || res.data;
        setWarehouses(Array.isArray(data) ? data : []);
      }).catch(() => {});
    });
  }, []);

  // Set pre-selected PO from query param
  useEffect(() => {
    if (preSelectedPO && !selectedPOId) {
      setSelectedPOId(preSelectedPO);
    }
  }, [preSelectedPO, selectedPOId]);

  // Initialize line quantities when PO loads
  useEffect(() => {
    if (selectedPO?.lines) {
      const qtys: Record<number, string> = {};
      selectedPO.lines.forEach((line) => {
        const outstanding = parseFloat(line.quantity) - parseFloat(line.qty_received);
        if (outstanding > 0) {
          qtys[line.id] = String(outstanding);
        }
      });
      setLineQtys(qtys);
    }
  }, [selectedPO]);

  const receivableLines = selectedPO?.lines.filter(
    (line) => parseFloat(line.quantity) - parseFloat(line.qty_received) > 0
  ) || [];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedPOId || !warehouseId) {
      toast({ title: "Error", description: "Please select a PO and warehouse.", variant: "destructive" });
      return;
    }

    const lines = Object.entries(lineQtys)
      .filter(([_, qty]) => parseFloat(qty) > 0)
      .map(([lineId, qty]) => ({
        po_line_id: parseInt(lineId),
        qty_received: qty,
      }));

    if (lines.length === 0) {
      toast({ title: "Error", description: "Enter quantities to receive.", variant: "destructive" });
      return;
    }

    setIsSubmitting(true);
    try {
      await createGR.mutateAsync({
        purchase_order_id: selectedPOId,
        warehouse_id: parseInt(warehouseId),
        receipt_date: receiptDate,
        notes,
        lines,
      });
      toast({ title: "Goods receipt created", description: "GRN has been created as a draft." });
      router.push("/accounting/goods-receipts");
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to create receipt.", variant: "destructive" });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <AppLayout>
      <form onSubmit={handleSubmit} className="space-y-6">
        <PageHeader title="New Goods Receipt" subtitle="Receive goods against a purchase order" actions={
          <div className="flex gap-2">
            <Link href="/accounting/goods-receipts"><Button type="button" variant="outline"><ArrowLeft className="h-4 w-4 me-2" />Cancel</Button></Link>
            <Button type="submit" disabled={isSubmitting || !selectedPOId}><Save className="h-4 w-4 me-2" />{isSubmitting ? "Saving..." : "Save Draft"}</Button>
          </div>
        } />

        <Card>
          <CardHeader><CardTitle>Receipt Details</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="space-y-2">
              <Label>GRN Number</Label>
              <Input value="Auto-generated" disabled className="bg-muted" />
            </div>
            <div className="space-y-2">
              <Label>Receipt Date *</Label>
              <CompanyDateInput id="receipt_date" value={receiptDate} onChange={setReceiptDate} dateFormat={(company?.date_format as any) || "YYYY-MM-DD"} />
            </div>
            <div className="space-y-2">
              <Label>Purchase Order *</Label>
              <Select value={selectedPOId ? String(selectedPOId) : ""} onValueChange={(val) => setSelectedPOId(parseInt(val))}>
                <SelectTrigger><SelectValue placeholder="Select PO" /></SelectTrigger>
                <SelectContent>
                  {availablePOs.map((po) => (
                    <SelectItem key={po.id} value={po.id.toString()}>
                      {po.order_number} - {po.vendor_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Warehouse *</Label>
              <Select value={warehouseId} onValueChange={setWarehouseId}>
                <SelectTrigger><SelectValue placeholder="Select warehouse" /></SelectTrigger>
                <SelectContent>
                  {warehouses.map((wh) => (
                    <SelectItem key={wh.id} value={wh.id.toString()}>
                      {wh.code} - {wh.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>Notes</Label>
              <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Receipt notes..." rows={2} />
            </div>
          </CardContent>
        </Card>

        {selectedPO && (
          <Card>
            <CardHeader><CardTitle>Lines to Receive</CardTitle></CardHeader>
            <CardContent>
              {receivableLines.length === 0 ? (
                <EmptyState icon={<Package className="h-12 w-12" />} title="Nothing to receive" description="All lines on this PO have been fully received." />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b text-sm text-muted-foreground">
                        <th className="text-start py-2 px-2">Line</th>
                        <th className="text-start py-2 px-2">Item</th>
                        <th className="text-start py-2 px-2">Description</th>
                        <th className="text-end py-2 px-2">Ordered</th>
                        <th className="text-end py-2 px-2">Already Received</th>
                        <th className="text-end py-2 px-2">Outstanding</th>
                        <th className="text-end py-2 px-2 w-[120px]">Qty to Receive</th>
                      </tr>
                    </thead>
                    <tbody>
                      {receivableLines.map((line) => {
                        const outstanding = parseFloat(line.quantity) - parseFloat(line.qty_received);
                        return (
                          <tr key={line.id} className="border-b">
                            <td className="py-2 px-2 text-sm">{line.line_number}</td>
                            <td className="py-2 px-2 text-sm font-mono">{line.account_code}</td>
                            <td className="py-2 px-2 text-sm">{line.description}</td>
                            <td className="py-2 px-2 text-end text-sm font-mono">{parseFloat(line.quantity).toFixed(2)}</td>
                            <td className="py-2 px-2 text-end text-sm font-mono">{parseFloat(line.qty_received).toFixed(2)}</td>
                            <td className="py-2 px-2 text-end text-sm font-mono font-medium">{outstanding.toFixed(2)}</td>
                            <td className="py-2 px-2">
                              <Input
                                type="number"
                                step="0.01"
                                min="0"
                                max={outstanding}
                                value={lineQtys[line.id] || "0"}
                                onChange={(e) => setLineQtys({ ...lineQtys, [line.id]: e.target.value })}
                                className="h-8 text-xs text-end w-full"
                              />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </form>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
