import React, { useEffect, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Save, Receipt } from "lucide-react";
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
import { useCreatePurchaseCreditNote } from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/AuthContext";
import { purchaseBillsService } from "@/services/purchases.service";
import type { PurchaseBillListItem } from "@/types/purchases";
import type { PurchaseCreditNoteReason } from "@/types/purchases";
import { PCN_REASON_LABELS } from "@/types/purchases";

interface BillDetail {
  id: number;
  bill_number: string;
  vendor_name: string;
  total_amount: string;
  lines: {
    id: number;
    line_number: number;
    description: string;
    quantity: string;
    unit_price: string;
    net_amount: string;
    tax_amount: string;
    line_total: string;
    account: number;
    account_code: string;
    tax_code: number | null;
    item: number | null;
  }[];
}

export default function NewPurchaseCreditNotePage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { company } = useAuth();
  const createCN = useCreatePurchaseCreditNote();

  const preSelectedBill = router.query.bill ? parseInt(router.query.bill as string) : null;
  const [selectedBillId, setSelectedBillId] = useState<number | null>(preSelectedBill);
  const [postedBills, setPostedBills] = useState<PurchaseBillListItem[]>([]);
  const [billDetail, setBillDetail] = useState<BillDetail | null>(null);
  const [creditDate, setCreditDate] = useState(new Date().toISOString().split("T")[0]);
  const [reason, setReason] = useState<PurchaseCreditNoteReason>("RETURN");
  const [reasonNotes, setReasonNotes] = useState("");
  const [notes, setNotes] = useState("");
  const [lineQtys, setLineQtys] = useState<Record<number, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Fetch posted bills
  useEffect(() => {
    purchaseBillsService.list({ status: "POSTED", page_size: 200 }).then((res) => {
      setPostedBills(res.data.results);
    }).catch(() => {});
  }, []);

  // Fetch bill detail when selected
  useEffect(() => {
    if (selectedBillId) {
      purchaseBillsService.get(selectedBillId).then((res) => {
        setBillDetail(res.data as any);
        const qtys: Record<number, string> = {};
        (res.data as any).lines?.forEach((line: any) => {
          qtys[line.id] = line.quantity;
        });
        setLineQtys(qtys);
      }).catch(() => {});
    }
  }, [selectedBillId]);

  useEffect(() => {
    if (preSelectedBill && !selectedBillId) {
      setSelectedBillId(preSelectedBill);
    }
  }, [preSelectedBill, selectedBillId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedBillId || !billDetail) {
      toast({ title: "Error", description: "Please select a purchase bill.", variant: "destructive" });
      return;
    }

    const lines = Object.entries(lineQtys)
      .filter(([_, qty]) => parseFloat(qty) > 0)
      .map(([lineId, qty]) => {
        const billLine = billDetail.lines.find((l) => l.id === parseInt(lineId));
        return {
          account_id: billLine?.account || 0,
          description: `Return: ${billLine?.description || ""}`,
          quantity: parseFloat(qty),
          unit_price: parseFloat(billLine?.unit_price || "0"),
          tax_code_id: billLine?.tax_code || undefined,
          item_id: billLine?.item || undefined,
          bill_line_id: parseInt(lineId),
        };
      })
      .filter((l) => l.account_id > 0);

    if (lines.length === 0) {
      toast({ title: "Error", description: "Enter quantities to return/credit.", variant: "destructive" });
      return;
    }

    setIsSubmitting(true);
    try {
      await createCN.mutateAsync({
        bill_id: selectedBillId,
        credit_note_date: creditDate,
        reason,
        reason_notes: reasonNotes,
        notes,
        lines,
      });
      toast({ title: "Credit note created", description: "Purchase credit note has been created as a draft." });
      router.push("/accounting/purchase-credit-notes");
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to create credit note.", variant: "destructive" });
    } finally {
      setIsSubmitting(false);
    }
  };

  const reasons: PurchaseCreditNoteReason[] = ["RETURN", "PRICE_ADJUSTMENT", "TAX_CORRECTION", "DAMAGED", "OTHER"];

  return (
    <AppLayout>
      <form onSubmit={handleSubmit} className="space-y-6">
        <PageHeader title="New Purchase Credit Note" subtitle="Create a vendor return or debit note against a posted bill" actions={
          <div className="flex gap-2">
            <Link href="/accounting/purchase-credit-notes"><Button type="button" variant="outline"><ArrowLeft className="h-4 w-4 me-2" />Cancel</Button></Link>
            <Button type="submit" disabled={isSubmitting || !selectedBillId}><Save className="h-4 w-4 me-2" />{isSubmitting ? "Saving..." : "Save Draft"}</Button>
          </div>
        } />

        <Card>
          <CardHeader><CardTitle>Credit Note Details</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="space-y-2">
              <Label>Credit Note Number</Label>
              <Input value="Auto-generated" disabled className="bg-muted" />
            </div>
            <div className="space-y-2">
              <Label>Date *</Label>
              <CompanyDateInput id="credit_date" value={creditDate} onChange={setCreditDate} dateFormat={(company?.date_format as any) || "YYYY-MM-DD"} />
            </div>
            <div className="space-y-2">
              <Label>Original Bill *</Label>
              <Select value={selectedBillId ? String(selectedBillId) : ""} onValueChange={(val) => setSelectedBillId(parseInt(val))}>
                <SelectTrigger><SelectValue placeholder="Select bill" /></SelectTrigger>
                <SelectContent>
                  {postedBills.map((bill) => (
                    <SelectItem key={bill.id} value={bill.id.toString()}>
                      {bill.bill_number} - {bill.vendor_name} ({parseFloat(bill.total_amount).toLocaleString(undefined, { minimumFractionDigits: 2 })})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Reason *</Label>
              <Select value={reason} onValueChange={(val) => setReason(val as PurchaseCreditNoteReason)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {reasons.map((r) => <SelectItem key={r} value={r}>{PCN_REASON_LABELS[r]}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>Reason Notes</Label>
              <Textarea value={reasonNotes} onChange={(e) => setReasonNotes(e.target.value)} placeholder="Explain the reason for this return..." rows={2} />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>Internal Notes</Label>
              <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Internal notes..." rows={2} />
            </div>
          </CardContent>
        </Card>

        {billDetail && (
          <Card>
            <CardHeader><CardTitle>Bill Lines to Credit</CardTitle></CardHeader>
            <CardContent>
              {billDetail.lines.length === 0 ? (
                <EmptyState icon={<Receipt className="h-12 w-12" />} title="No lines" description="This bill has no lines." />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b text-sm text-muted-foreground">
                        <th className="text-start py-2 px-2">Line</th>
                        <th className="text-start py-2 px-2">Description</th>
                        <th className="text-start py-2 px-2">Account</th>
                        <th className="text-end py-2 px-2">Original Qty</th>
                        <th className="text-end py-2 px-2">Unit Price</th>
                        <th className="text-end py-2 px-2">Line Total</th>
                        <th className="text-end py-2 px-2 w-[120px]">Qty to Return</th>
                      </tr>
                    </thead>
                    <tbody>
                      {billDetail.lines.map((line) => (
                        <tr key={line.id} className="border-b">
                          <td className="py-2 px-2 text-sm">{line.line_number}</td>
                          <td className="py-2 px-2 text-sm">{line.description}</td>
                          <td className="py-2 px-2 text-sm font-mono">{line.account_code}</td>
                          <td className="py-2 px-2 text-end text-sm font-mono">{parseFloat(line.quantity).toFixed(2)}</td>
                          <td className="py-2 px-2 text-end text-sm font-mono">{parseFloat(line.unit_price).toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                          <td className="py-2 px-2 text-end text-sm font-mono font-medium">{parseFloat(line.line_total).toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                          <td className="py-2 px-2">
                            <Input
                              type="number"
                              step="0.01"
                              min="0"
                              max={parseFloat(line.quantity)}
                              value={lineQtys[line.id] || "0"}
                              onChange={(e) => setLineQtys({ ...lineQtys, [line.id]: e.target.value })}
                              className="h-8 text-xs text-end w-full"
                            />
                          </td>
                        </tr>
                      ))}
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
