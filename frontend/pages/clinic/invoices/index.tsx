import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { Plus, ClipboardCheck, Send, Trash2 } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useClinicInvoices, useCreateClinicInvoice, useIssueClinicInvoice, usePatients } from "@/queries/useClinic";
import { cn } from "@/lib/cn";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toaster";

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  issued: "bg-blue-100 text-blue-800",
  paid: "bg-green-100 text-green-800",
  partially_paid: "bg-yellow-100 text-yellow-800",
  cancelled: "bg-red-100 text-red-800",
};

export default function InvoicesPage() {
  const { data: invoices, isLoading } = useClinicInvoices();
  const { data: patients } = usePatients();
  const createInvoice = useCreateClinicInvoice();
  const issueInvoice = useIssueClinicInvoice();
  const { toast } = useToast();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    patient_id: "",
    date: new Date().toISOString().split("T")[0],
    notes: "",
  });
  const [lines, setLines] = useState([{ description: "", amount: "" }]);

  const addLine = () => setLines([...lines, { description: "", amount: "" }]);
  const removeLine = (i: number) => setLines(lines.filter((_, idx) => idx !== i));
  const updateLine = (i: number, field: string, value: string) => {
    const updated = [...lines];
    (updated[i] as any)[field] = value;
    setLines(updated);
  };

  const handleCreate = async () => {
    try {
      const validLines = lines.filter((l) => l.description && l.amount);
      await createInvoice.mutateAsync({
        patient_id: Number(form.patient_id),
        date: form.date,
        line_items: validLines.map((l) => ({ description: l.description, amount: Number(l.amount) })),
        notes: form.notes,
      });
      toast({ title: "Invoice created" });
      setShowCreate(false);
      setForm({ patient_id: "", date: new Date().toISOString().split("T")[0], notes: "" });
      setLines([{ description: "", amount: "" }]);
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Failed to create invoice", variant: "destructive" });
    }
  };

  const handleIssue = async (id: number) => {
    try {
      await issueInvoice.mutateAsync(id);
      toast({ title: "Invoice issued" });
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Failed to issue invoice", variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Invoices"
          subtitle="Manage clinic invoices"
          actions={
            <Button onClick={() => setShowCreate(true)}>
              <Plus className="mr-2 h-4 w-4" />
              New Invoice
            </Button>
          }
        />

        {isLoading ? (
          <LoadingSpinner />
        ) : !invoices?.length ? (
          <EmptyState
            icon={<ClipboardCheck className="h-12 w-12 text-muted-foreground" />}
            title="No invoices found"
            description="Create your first invoice."
          />
        ) : (
          <div className="space-y-3">
            {invoices.map((inv) => (
              <Card key={inv.id}>
                <CardContent className="p-4 flex items-center justify-between">
                  <div>
                    <p className="font-semibold">{inv.invoice_no}</p>
                    <p className="text-sm text-muted-foreground">
                      {inv.patient_name} ({inv.patient_code}) &middot; {inv.date}
                    </p>
                    <p className="text-sm">
                      Total: {inv.total} {inv.currency} &middot; Paid: {inv.amount_paid} &middot; Due: {inv.balance_due}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge className={cn(STATUS_COLORS[inv.status])}>{inv.status}</Badge>
                    {inv.status === "draft" && (
                      <Button size="sm" variant="outline" onClick={() => handleIssue(inv.id)} disabled={issueInvoice.isPending}>
                        <Send className="mr-1 h-3 w-3" />Issue
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>New Invoice</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Patient *</Label>
              <select className="w-full border rounded-md px-3 py-2 text-sm" value={form.patient_id} onChange={(e) => setForm({ ...form, patient_id: e.target.value })}>
                <option value="">Select patient...</option>
                {patients?.map((p) => <option key={p.id} value={p.id}>{p.code} - {p.name}</option>)}
              </select>
            </div>
            <div>
              <Label>Date *</Label>
              <Input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />
            </div>

            <div>
              <Label>Line Items</Label>
              <div className="space-y-2 mt-1">
                {lines.map((line, i) => (
                  <div key={i} className="flex gap-2">
                    <Input placeholder="Description" value={line.description} onChange={(e) => updateLine(i, "description", e.target.value)} className="flex-1" />
                    <Input placeholder="Amount" type="number" value={line.amount} onChange={(e) => updateLine(i, "amount", e.target.value)} className="w-28" />
                    {lines.length > 1 && (
                      <Button variant="ghost" size="icon" onClick={() => removeLine(i)}>
                        <Trash2 className="h-4 w-4 text-red-400" />
                      </Button>
                    )}
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={addLine}>+ Add Line</Button>
              </div>
            </div>

            <div>
              <Label>Notes</Label>
              <Input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </div>

            <Button className="w-full" onClick={handleCreate} disabled={!form.patient_id || createInvoice.isPending}>
              {createInvoice.isPending ? "Creating..." : "Create Invoice"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: { ...(await serverSideTranslations(locale ?? "en", ["common"])) },
});
