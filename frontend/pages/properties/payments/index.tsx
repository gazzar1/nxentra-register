import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { Search, DollarSign, Plus } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { usePayments, useLeases, useCreatePayment } from "@/queries/useProperties";
import type { PaymentMethod } from "@/types/properties";
import { useToast } from "@/components/ui/toaster";
import { cn } from "@/lib/cn";

const ALLOCATION_STATUS_COLORS: Record<string, string> = {
  unallocated: "bg-gray-100 text-gray-800",
  partially_allocated: "bg-orange-100 text-orange-800",
  fully_allocated: "bg-green-100 text-green-800",
};

const PAYMENT_METHOD_LABELS: Record<string, string> = {
  cash: "Cash",
  bank_transfer: "Bank Transfer",
  cheque: "Cheque",
  credit_card: "Credit Card",
  online: "Online",
  other: "Other",
};

export default function PaymentsPage() {
  const router = useRouter();
  const { data: payments, isLoading } = usePayments();
  const { data: leases } = useLeases({ status: "active" });
  const createPayment = useCreatePayment();
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [paymentOpen, setPaymentOpen] = useState(false);
  const [paymentForm, setPaymentForm] = useState({
    lease_id: "",
    receipt_no: "",
    amount: "",
    payment_date: "",
    method: "bank_transfer" as PaymentMethod,
    reference_no: "",
    notes: "",
  });

  const filtered = payments?.filter((p) => {
    if (!search) return true;
    const s = search.toLowerCase();
    return (
      p.receipt_no.toLowerCase().includes(s) ||
      p.lessee_name.toLowerCase().includes(s) ||
      p.lease_contract_no.toLowerCase().includes(s)
    );
  });

  const handleCreatePayment = async () => {
    try {
      await createPayment.mutateAsync({
        receipt_no: paymentForm.receipt_no,
        lease_id: Number(paymentForm.lease_id),
        amount: Number(paymentForm.amount),
        payment_date: paymentForm.payment_date,
        method: paymentForm.method,
        reference_no: paymentForm.reference_no || null,
        notes: paymentForm.notes || null,
      });
      setPaymentOpen(false);
      setPaymentForm({
        lease_id: "",
        receipt_no: "",
        amount: "",
        payment_date: "",
        method: "bank_transfer",
        reference_no: "",
        notes: "",
      });
      toast({ title: "Payment recorded" });
    } catch (err: any) {
      toast({
        title: "Payment failed",
        description: err?.response?.data?.detail || "Could not record payment.",
        variant: "destructive",
      });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Payments"
          subtitle="Rent payment receipts"
          actions={
            <Button onClick={() => setPaymentOpen(true)}>
              <Plus className="mr-1 h-4 w-4" />
              Record Payment
            </Button>
          }
        />

        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search payments..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
        </div>

        {isLoading ? (
          <LoadingSpinner />
        ) : !filtered?.length ? (
          <EmptyState
            icon={<DollarSign className="h-12 w-12" />}
            title="No payments found"
            description="No payments recorded yet. Click 'Record Payment' to add one."
          />
        ) : (
          <div className="rounded-lg border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium">Receipt #</th>
                  <th className="px-4 py-3 text-left font-medium">Lease</th>
                  <th className="px-4 py-3 text-left font-medium">Lessee</th>
                  <th className="px-4 py-3 text-left font-medium">Date</th>
                  <th className="px-4 py-3 text-right font-medium">Amount</th>
                  <th className="px-4 py-3 text-left font-medium">Method</th>
                  <th className="px-4 py-3 text-center font-medium">Allocation Status</th>
                  <th className="px-4 py-3 text-center font-medium">Voided</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((payment) => (
                  <tr
                    key={payment.id}
                    className="border-b hover:bg-muted/30 cursor-pointer"
                    onClick={() => router.push(`/properties/leases/${payment.lease}`)}
                  >
                    <td className="px-4 py-3 font-medium">{payment.receipt_no}</td>
                    <td className="px-4 py-3">{payment.lease_contract_no}</td>
                    <td className="px-4 py-3">{payment.lessee_name}</td>
                    <td className="px-4 py-3 text-muted-foreground">{payment.payment_date}</td>
                    <td className="px-4 py-3 text-right">
                      {Number(payment.amount).toLocaleString()} {payment.currency}
                    </td>
                    <td className="px-4 py-3">{PAYMENT_METHOD_LABELS[payment.method] || payment.method}</td>
                    <td className="px-4 py-3 text-center">
                      <Badge className={cn("text-xs", ALLOCATION_STATUS_COLORS[payment.allocation_status])}>
                        {payment.allocation_status.replace(/_/g, " ")}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-center">
                      {payment.voided && (
                        <Badge className="text-xs bg-red-100 text-red-800">
                          Voided
                        </Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Record Payment Dialog */}
      <Dialog open={paymentOpen} onOpenChange={setPaymentOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Record Payment</DialogTitle>
            <DialogDescription>
              Record a rent payment for an active lease.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="pay_lease">Lease</Label>
              <Select
                value={paymentForm.lease_id}
                onValueChange={(v) => setPaymentForm({ ...paymentForm, lease_id: v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a lease..." />
                </SelectTrigger>
                <SelectContent>
                  {leases?.map((l) => (
                    <SelectItem key={l.id} value={String(l.id)}>
                      {l.contract_no} — {l.lessee_name} ({l.property_code})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="pay_receipt_no">Receipt No</Label>
              <Input
                id="pay_receipt_no"
                value={paymentForm.receipt_no}
                onChange={(e) => setPaymentForm({ ...paymentForm, receipt_no: e.target.value })}
                placeholder="e.g., RCP-001"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="pay_amount">Amount</Label>
                <Input
                  id="pay_amount"
                  type="number"
                  value={paymentForm.amount}
                  onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="pay_date">Date</Label>
                <Input
                  id="pay_date"
                  type="date"
                  value={paymentForm.payment_date}
                  onChange={(e) => setPaymentForm({ ...paymentForm, payment_date: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="pay_method">Method</Label>
              <Select
                value={paymentForm.method}
                onValueChange={(v) => setPaymentForm({ ...paymentForm, method: v as PaymentMethod })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="cash">Cash</SelectItem>
                  <SelectItem value="bank_transfer">Bank Transfer</SelectItem>
                  <SelectItem value="cheque">Cheque</SelectItem>
                  <SelectItem value="credit_card">Credit Card</SelectItem>
                  <SelectItem value="online">Online</SelectItem>
                  <SelectItem value="other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="pay_ref">Reference No (optional)</Label>
              <Input
                id="pay_ref"
                value={paymentForm.reference_no}
                onChange={(e) => setPaymentForm({ ...paymentForm, reference_no: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="pay_notes">Notes (optional)</Label>
              <Input
                id="pay_notes"
                value={paymentForm.notes}
                onChange={(e) => setPaymentForm({ ...paymentForm, notes: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPaymentOpen(false)}>Cancel</Button>
            <Button
              onClick={handleCreatePayment}
              disabled={
                !paymentForm.lease_id ||
                !paymentForm.receipt_no ||
                !paymentForm.amount ||
                !paymentForm.payment_date ||
                createPayment.isPending
              }
            >
              {createPayment.isPending ? "Saving..." : "Record Payment"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
