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
import { useCreateCreditNote } from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/AuthContext";
import { salesInvoicesService } from "@/services/sales.service";
import type { SalesInvoiceListItem } from "@/types/sales";
import type { CreditNoteReason } from "@/types/sales";
import { CREDIT_NOTE_REASON_LABELS } from "@/types/sales";

interface InvoiceDetail {
  id: number;
  invoice_number: string;
  customer_name: string;
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
  }[];
}

export default function NewCreditNotePage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { company } = useAuth();
  const createCN = useCreateCreditNote();

  const preSelectedInvoice = router.query.invoice ? parseInt(router.query.invoice as string) : null;
  const [selectedInvoiceId, setSelectedInvoiceId] = useState<number | null>(preSelectedInvoice);
  const [postedInvoices, setPostedInvoices] = useState<SalesInvoiceListItem[]>([]);
  const [invoiceDetail, setInvoiceDetail] = useState<InvoiceDetail | null>(null);
  const [creditDate, setCreditDate] = useState(new Date().toISOString().split("T")[0]);
  const [reason, setReason] = useState<CreditNoteReason>("OTHER");
  const [reasonNotes, setReasonNotes] = useState("");
  const [notes, setNotes] = useState("");
  const [lineQtys, setLineQtys] = useState<Record<number, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Fetch posted invoices
  useEffect(() => {
    salesInvoicesService.list({ status: "POSTED", page_size: 200 }).then((res) => {
      setPostedInvoices(res.data.results);
    }).catch(() => {});
  }, []);

  // Fetch invoice detail when selected
  useEffect(() => {
    if (selectedInvoiceId) {
      salesInvoicesService.get(selectedInvoiceId).then((res) => {
        setInvoiceDetail(res.data as any);
        // Initialize qtys from invoice lines
        const qtys: Record<number, string> = {};
        (res.data as any).lines?.forEach((line: any) => {
          qtys[line.id] = line.quantity;
        });
        setLineQtys(qtys);
      }).catch(() => {});
    }
  }, [selectedInvoiceId]);

  useEffect(() => {
    if (preSelectedInvoice && !selectedInvoiceId) {
      setSelectedInvoiceId(preSelectedInvoice);
    }
  }, [preSelectedInvoice, selectedInvoiceId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedInvoiceId || !invoiceDetail) {
      toast({ title: "Error", description: "Please select an invoice.", variant: "destructive" });
      return;
    }

    const lines = Object.entries(lineQtys)
      .filter(([_, qty]) => parseFloat(qty) > 0)
      .map(([lineId, qty]) => {
        const invLine = invoiceDetail.lines.find((l) => l.id === parseInt(lineId));
        return {
          account_id: invLine?.account || 0,
          description: `Credit: ${invLine?.description || ""}`,
          quantity: parseFloat(qty),
          unit_price: parseFloat(invLine?.unit_price || "0"),
          tax_code_id: invLine?.tax_code || undefined,
          invoice_line_id: parseInt(lineId),
        };
      })
      .filter((l) => l.account_id > 0);

    if (lines.length === 0) {
      toast({ title: "Error", description: "Enter quantities to credit.", variant: "destructive" });
      return;
    }

    setIsSubmitting(true);
    try {
      await createCN.mutateAsync({
        invoice_id: selectedInvoiceId,
        credit_note_date: creditDate,
        reason,
        reason_notes: reasonNotes,
        notes,
        lines,
      });
      toast({ title: "Credit note created", description: "Credit note has been created as a draft." });
      router.push("/accounting/credit-notes");
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to create credit note.", variant: "destructive" });
    } finally {
      setIsSubmitting(false);
    }
  };

  const reasons: CreditNoteReason[] = ["RETURN", "PRICE_ADJUSTMENT", "TAX_CORRECTION", "DAMAGED", "OTHER"];

  return (
    <AppLayout>
      <form onSubmit={handleSubmit} className="space-y-6">
        <PageHeader title="New Credit Note" subtitle="Create a credit note against a posted invoice" actions={
          <div className="flex gap-2">
            <Link href="/accounting/credit-notes"><Button type="button" variant="outline"><ArrowLeft className="h-4 w-4 me-2" />Cancel</Button></Link>
            <Button type="submit" disabled={isSubmitting || !selectedInvoiceId}><Save className="h-4 w-4 me-2" />{isSubmitting ? "Saving..." : "Save Draft"}</Button>
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
              <Label>Original Invoice *</Label>
              <Select value={selectedInvoiceId ? String(selectedInvoiceId) : ""} onValueChange={(val) => setSelectedInvoiceId(parseInt(val))}>
                <SelectTrigger><SelectValue placeholder="Select invoice" /></SelectTrigger>
                <SelectContent>
                  {postedInvoices.map((inv) => (
                    <SelectItem key={inv.id} value={inv.id.toString()}>
                      {inv.invoice_number} - {inv.customer_name} ({parseFloat(inv.total_amount).toLocaleString(undefined, { minimumFractionDigits: 2 })})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Reason *</Label>
              <Select value={reason} onValueChange={(val) => setReason(val as CreditNoteReason)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {reasons.map((r) => <SelectItem key={r} value={r}>{CREDIT_NOTE_REASON_LABELS[r]}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>Reason Notes</Label>
              <Textarea value={reasonNotes} onChange={(e) => setReasonNotes(e.target.value)} placeholder="Explain the reason..." rows={2} />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>Internal Notes</Label>
              <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Internal notes..." rows={2} />
            </div>
          </CardContent>
        </Card>

        {invoiceDetail && (
          <Card>
            <CardHeader><CardTitle>Invoice Lines to Credit</CardTitle></CardHeader>
            <CardContent>
              {invoiceDetail.lines.length === 0 ? (
                <EmptyState icon={<Receipt className="h-12 w-12" />} title="No lines" description="This invoice has no lines." />
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
                        <th className="text-end py-2 px-2 w-[120px]">Qty to Credit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoiceDetail.lines.map((line) => (
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
