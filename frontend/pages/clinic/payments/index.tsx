import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { Plus, Banknote, XCircle } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useClinicPayments, useCreateClinicPayment, useVoidClinicPayment, useClinicInvoices } from "@/queries/useClinic";
import { cn } from "@/lib/cn";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toaster";

const STATUS_COLORS: Record<string, string> = {
  completed: "bg-green-100 text-green-800",
  voided: "bg-red-100 text-red-800",
};

export default function PaymentsPage() {
  const { data: payments, isLoading } = useClinicPayments();
  const { data: invoices } = useClinicInvoices({ status: "issued" });
  const createPayment = useCreateClinicPayment();
  const voidPayment = useVoidClinicPayment();
  const { toast } = useToast();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    invoice_id: "",
    amount: "",
    payment_method: "cash",
    payment_date: new Date().toISOString().split("T")[0],
    reference: "",
  });

  const handleCreate = async () => {
    try {
      await createPayment.mutateAsync({
        invoice_id: Number(form.invoice_id),
        amount: Number(form.amount),
        payment_method: form.payment_method as any,
        payment_date: form.payment_date,
        reference: form.reference,
      });
      toast({ title: "Payment recorded" });
      setShowCreate(false);
      setForm({ invoice_id: "", amount: "", payment_method: "cash", payment_date: new Date().toISOString().split("T")[0], reference: "" });
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Failed to record payment", variant: "destructive" });
    }
  };

  const handleVoid = async (id: number) => {
    if (!confirm("Are you sure you want to void this payment?")) return;
    try {
      await voidPayment.mutateAsync({ id, data: { reason: "Voided by user" } });
      toast({ title: "Payment voided" });
    } catch (e: any) {
      toast({ title: e?.response?.data?.detail || "Failed to void payment", variant: "destructive" });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Payments"
          subtitle="Track clinic payments"
          actions={
            <Button onClick={() => setShowCreate(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Record Payment
            </Button>
          }
        />

        {isLoading ? (
          <LoadingSpinner />
        ) : !payments?.length ? (
          <EmptyState
            icon={<Banknote className="h-12 w-12 text-muted-foreground" />}
            title="No payments found"
            description="Record your first payment."
          />
        ) : (
          <div className="space-y-3">
            {payments.map((pmt) => (
              <Card key={pmt.id}>
                <CardContent className="p-4 flex items-center justify-between">
                  <div>
                    <p className="font-semibold">{pmt.amount} {pmt.currency}</p>
                    <p className="text-sm text-muted-foreground">
                      Invoice: {pmt.invoice_no} &middot; {pmt.patient_name} &middot; {pmt.payment_date}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      Method: {pmt.payment_method} {pmt.reference && `(${pmt.reference})`}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge className={cn(STATUS_COLORS[pmt.status])}>{pmt.status}</Badge>
                    {pmt.status === "completed" && (
                      <Button size="sm" variant="ghost" onClick={() => handleVoid(pmt.id)}>
                        <XCircle className="h-4 w-4 text-red-400" />
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
        <DialogContent>
          <DialogHeader><DialogTitle>Record Payment</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Invoice *</Label>
              <select className="w-full border rounded-md px-3 py-2 text-sm" value={form.invoice_id} onChange={(e) => setForm({ ...form, invoice_id: e.target.value })}>
                <option value="">Select invoice...</option>
                {invoices?.map((inv) => (
                  <option key={inv.id} value={inv.id}>
                    {inv.invoice_no} - {inv.patient_name} ({inv.balance_due} due)
                  </option>
                ))}
              </select>
            </div>
            <div>
              <Label>Amount *</Label>
              <Input type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} placeholder="0.00" />
            </div>
            <div>
              <Label>Method</Label>
              <select className="w-full border rounded-md px-3 py-2 text-sm" value={form.payment_method} onChange={(e) => setForm({ ...form, payment_method: e.target.value })}>
                <option value="cash">Cash</option>
                <option value="card">Card</option>
                <option value="transfer">Bank Transfer</option>
              </select>
            </div>
            <div>
              <Label>Date *</Label>
              <Input type="date" value={form.payment_date} onChange={(e) => setForm({ ...form, payment_date: e.target.value })} />
            </div>
            <div>
              <Label>Reference</Label>
              <Input value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} placeholder="Receipt #" />
            </div>
            <Button className="w-full" onClick={handleCreate} disabled={!form.invoice_id || !form.amount || createPayment.isPending}>
              {createPayment.isPending ? "Recording..." : "Record Payment"}
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
