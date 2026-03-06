import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { useState } from "react";
import { Play, XCircle, RefreshCw, DollarSign, Ban, Shield } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useLease,
  useLeaseSchedule,
  useActivateLease,
  useTerminateLease,
  useRenewLease,
  useWaiveScheduleLine,
  usePayments,
  useCreatePayment,
  useDeposits,
  useCreateDeposit,
} from "@/queries/useProperties";
import type { PaymentMethod, DepositTransactionType } from "@/types/properties";
import { useToast } from "@/components/ui/toaster";
import { cn } from "@/lib/cn";

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  active: "bg-green-100 text-green-800",
  expired: "bg-yellow-100 text-yellow-800",
  terminated: "bg-red-100 text-red-800",
  renewed: "bg-blue-100 text-blue-800",
};

const SCHEDULE_STATUS_COLORS: Record<string, string> = {
  upcoming: "bg-gray-100 text-gray-800",
  due: "bg-yellow-100 text-yellow-800",
  overdue: "bg-red-100 text-red-800",
  partially_paid: "bg-orange-100 text-orange-800",
  paid: "bg-green-100 text-green-800",
  waived: "bg-blue-100 text-blue-800",
};

const DEPOSIT_TYPE_COLORS: Record<string, string> = {
  received: "bg-green-100 text-green-800",
  adjusted: "bg-blue-100 text-blue-800",
  refunded: "bg-yellow-100 text-yellow-800",
  forfeited: "bg-red-100 text-red-800",
};

const PAYMENT_METHOD_LABELS: Record<string, string> = {
  cash: "Cash",
  bank_transfer: "Bank Transfer",
  cheque: "Cheque",
  credit_card: "Credit Card",
  online: "Online",
  other: "Other",
};

const ALLOCATION_STATUS_COLORS: Record<string, string> = {
  unallocated: "bg-gray-100 text-gray-800",
  partially_allocated: "bg-orange-100 text-orange-800",
  fully_allocated: "bg-green-100 text-green-800",
};

function InfoRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex justify-between py-2 border-b last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium">{value || "—"}</span>
    </div>
  );
}

export default function LeaseDetailPage() {
  const router = useRouter();
  const id = Number(router.query.id);
  const { data: lease, isLoading } = useLease(id);
  const { data: schedule, isLoading: scheduleLoading } = useLeaseSchedule(id);
  const activateLease = useActivateLease();
  const terminateLease = useTerminateLease();
  const renewLease = useRenewLease();
  const { toast } = useToast();

  const [terminateOpen, setTerminateOpen] = useState(false);
  const [terminationReason, setTerminationReason] = useState("");
  const [renewOpen, setRenewOpen] = useState(false);
  const [renewForm, setRenewForm] = useState({
    new_contract_no: "",
    new_start_date: "",
    new_end_date: "",
    new_rent_amount: "",
  });

  const waiveScheduleLine = useWaiveScheduleLine();
  const { data: payments, isLoading: paymentsLoading } = usePayments({ lease: id });
  const createPayment = useCreatePayment();
  const { data: deposits, isLoading: depositsLoading } = useDeposits({ lease: id });
  const createDeposit = useCreateDeposit();

  const [waiveOpen, setWaiveOpen] = useState(false);
  const [waiveLineId, setWaiveLineId] = useState<number | null>(null);
  const [waiveReason, setWaiveReason] = useState("");

  const [paymentOpen, setPaymentOpen] = useState(false);
  const [paymentForm, setPaymentForm] = useState({
    receipt_no: "",
    amount: "",
    payment_date: "",
    method: "bank_transfer" as PaymentMethod,
    reference_no: "",
    notes: "",
  });

  const [depositOpen, setDepositOpen] = useState(false);
  const [depositForm, setDepositForm] = useState({
    transaction_type: "received" as DepositTransactionType,
    amount: "",
    transaction_date: "",
    reason: "",
    reference: "",
  });

  if (isLoading) return <AppLayout><LoadingSpinner /></AppLayout>;
  if (!lease) return <AppLayout><div className="p-6">Lease not found</div></AppLayout>;

  const handleActivate = async () => {
    try {
      await activateLease.mutateAsync(id);
      toast({ title: "Lease activated", description: "Rent schedule has been generated." });
    } catch (err: any) {
      toast({
        title: "Activation failed",
        description: err?.response?.data?.detail || "Could not activate lease.",
        variant: "destructive",
      });
    }
  };

  const handleTerminate = async () => {
    try {
      await terminateLease.mutateAsync({ id, termination_reason: terminationReason });
      setTerminateOpen(false);
      setTerminationReason("");
      toast({ title: "Lease terminated" });
    } catch (err: any) {
      toast({
        title: "Termination failed",
        description: err?.response?.data?.detail || "Could not terminate lease.",
        variant: "destructive",
      });
    }
  };

  const handleRenew = async () => {
    try {
      await renewLease.mutateAsync({
        id,
        new_contract_no: renewForm.new_contract_no,
        new_start_date: renewForm.new_start_date,
        new_end_date: renewForm.new_end_date,
        new_rent_amount: renewForm.new_rent_amount ? Number(renewForm.new_rent_amount) : undefined,
      });
      setRenewOpen(false);
      toast({ title: "Lease renewed", description: "A new draft lease has been created." });
    } catch (err: any) {
      toast({
        title: "Renewal failed",
        description: err?.response?.data?.detail || "Could not renew lease.",
        variant: "destructive",
      });
    }
  };

  const handleWaive = async () => {
    if (!waiveLineId) return;
    try {
      await waiveScheduleLine.mutateAsync({ id: waiveLineId, reason: waiveReason });
      setWaiveOpen(false);
      setWaiveLineId(null);
      setWaiveReason("");
      toast({ title: "Schedule line waived" });
    } catch (err: any) {
      toast({
        title: "Waive failed",
        description: err?.response?.data?.detail || "Could not waive schedule line.",
        variant: "destructive",
      });
    }
  };

  const handleCreatePayment = async () => {
    try {
      await createPayment.mutateAsync({
        receipt_no: paymentForm.receipt_no,
        lease_id: id,
        amount: Number(paymentForm.amount),
        payment_date: paymentForm.payment_date,
        method: paymentForm.method,
        reference_no: paymentForm.reference_no || null,
        notes: paymentForm.notes || null,
      });
      setPaymentOpen(false);
      setPaymentForm({ receipt_no: "", amount: "", payment_date: "", method: "bank_transfer", reference_no: "", notes: "" });
      toast({ title: "Payment recorded" });
    } catch (err: any) {
      toast({
        title: "Payment failed",
        description: err?.response?.data?.detail || "Could not record payment.",
        variant: "destructive",
      });
    }
  };

  const handleCreateDeposit = async () => {
    try {
      await createDeposit.mutateAsync({
        lease_id: id,
        transaction_type: depositForm.transaction_type,
        amount: Number(depositForm.amount),
        transaction_date: depositForm.transaction_date,
        reason: depositForm.reason || null,
        reference: depositForm.reference || null,
      });
      setDepositOpen(false);
      setDepositForm({ transaction_type: "received", amount: "", transaction_date: "", reason: "", reference: "" });
      toast({ title: "Deposit transaction recorded" });
    } catch (err: any) {
      toast({
        title: "Deposit failed",
        description: err?.response?.data?.detail || "Could not record deposit.",
        variant: "destructive",
      });
    }
  };

  const totalScheduled = schedule?.reduce((sum, l) => sum + Number(l.total_due), 0) ?? 0;
  const totalPaid = schedule?.reduce((sum, l) => sum + Number(l.total_allocated), 0) ?? 0;
  const totalOutstanding = schedule?.reduce((sum, l) => sum + Number(l.outstanding), 0) ?? 0;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={`Lease ${lease.contract_no}`}
          subtitle={`${lease.property_code} - ${lease.property_name}`}
          actions={
            <div className="flex items-center gap-2">
              <Badge className={cn("text-sm", STATUS_COLORS[lease.status])}>
                {lease.status}
              </Badge>
              {lease.status === "draft" && (
                <Button
                  size="sm"
                  onClick={handleActivate}
                  disabled={activateLease.isPending}
                >
                  <Play className="mr-1 h-4 w-4" />
                  {activateLease.isPending ? "Activating..." : "Activate"}
                </Button>
              )}
              {lease.status === "active" && (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setRenewOpen(true)}
                  >
                    <RefreshCw className="mr-1 h-4 w-4" />
                    Renew
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setTerminateOpen(true)}
                  >
                    <XCircle className="mr-1 h-4 w-4" />
                    Terminate
                  </Button>
                </>
              )}
            </div>
          }
        />

        <Tabs defaultValue="details">
          <TabsList>
            <TabsTrigger value="details">Details</TabsTrigger>
            <TabsTrigger value="schedule">
              Rent Schedule {schedule?.length ? `(${schedule.length})` : ""}
            </TabsTrigger>
            <TabsTrigger value="payments">
              Payments {payments?.length ? `(${payments.length})` : ""}
            </TabsTrigger>
            <TabsTrigger value="deposits">
              Deposits {deposits?.length ? `(${deposits.length})` : ""}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="details" className="mt-4">
            <div className="grid gap-6 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Contract Details</CardTitle>
                </CardHeader>
                <CardContent>
                  <InfoRow label="Contract No" value={lease.contract_no} />
                  <InfoRow label="Property" value={`${lease.property_code} - ${lease.property_name}`} />
                  <InfoRow label="Unit" value={lease.unit_code || "Whole property"} />
                  <InfoRow label="Lessee" value={`${lease.lessee_code} - ${lease.lessee_name}`} />
                  <InfoRow label="Status" value={lease.status} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Financial Terms</CardTitle>
                </CardHeader>
                <CardContent>
                  <InfoRow label="Rent Amount" value={`${Number(lease.rent_amount).toLocaleString()} ${lease.currency}`} />
                  <InfoRow label="Payment Frequency" value={lease.payment_frequency} />
                  <InfoRow label="Security Deposit" value={`${Number(lease.deposit_amount).toLocaleString()} ${lease.currency}`} />
                  <InfoRow label="Grace Days" value={String(lease.grace_days)} />
                  <InfoRow label="Due Day Rule" value={lease.due_day_rule.replace("_", " ")} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Dates</CardTitle>
                </CardHeader>
                <CardContent>
                  <InfoRow label="Start Date" value={lease.start_date} />
                  <InfoRow label="End Date" value={lease.end_date} />
                  <InfoRow label="Handover Date" value={lease.handover_date} />
                  <InfoRow label="Activated At" value={lease.activated_at} />
                  <InfoRow label="Terminated At" value={lease.terminated_at} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Additional Info</CardTitle>
                </CardHeader>
                <CardContent>
                  <InfoRow label="Renewal Option" value={lease.renewal_option ? "Yes" : "No"} />
                  <InfoRow label="Notice Period" value={lease.notice_period_days ? `${lease.notice_period_days} days` : null} />
                  <InfoRow label="Document Ref" value={lease.document_ref} />
                  {lease.terms_summary && (
                    <div className="mt-2 pt-2 border-t">
                      <span className="text-sm text-muted-foreground">Terms Summary</span>
                      <p className="text-sm mt-1">{lease.terms_summary}</p>
                    </div>
                  )}
                  {lease.termination_reason && (
                    <div className="mt-2 pt-2 border-t">
                      <span className="text-sm text-muted-foreground">Termination Reason</span>
                      <p className="text-sm mt-1">{lease.termination_reason}</p>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          <TabsContent value="schedule" className="mt-4">
            {scheduleLoading ? (
              <LoadingSpinner />
            ) : !schedule?.length ? (
              <Card>
                <CardContent className="py-8 text-center text-muted-foreground">
                  {lease.status === "draft"
                    ? "Rent schedule will be generated when the lease is activated."
                    : "No rent schedule lines found."}
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-4">
                <div className="grid gap-4 md:grid-cols-3">
                  <Card>
                    <CardContent className="pt-4">
                      <div className="text-sm text-muted-foreground">Total Scheduled</div>
                      <div className="text-xl font-bold">
                        {totalScheduled.toLocaleString()} {lease.currency}
                      </div>
                    </CardContent>
                  </Card>
                  <Card>
                    <CardContent className="pt-4">
                      <div className="text-sm text-muted-foreground">Total Paid</div>
                      <div className="text-xl font-bold text-green-600">
                        {totalPaid.toLocaleString()} {lease.currency}
                      </div>
                    </CardContent>
                  </Card>
                  <Card>
                    <CardContent className="pt-4">
                      <div className="text-sm text-muted-foreground">Outstanding</div>
                      <div className="text-xl font-bold text-red-600">
                        {totalOutstanding.toLocaleString()} {lease.currency}
                      </div>
                    </CardContent>
                  </Card>
                </div>

                <div className="rounded-lg border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b bg-muted/50">
                        <th className="px-4 py-3 text-left font-medium">#</th>
                        <th className="px-4 py-3 text-left font-medium">Period</th>
                        <th className="px-4 py-3 text-left font-medium">Due Date</th>
                        <th className="px-4 py-3 text-right font-medium">Base Rent</th>
                        <th className="px-4 py-3 text-right font-medium">Total Due</th>
                        <th className="px-4 py-3 text-right font-medium">Paid</th>
                        <th className="px-4 py-3 text-right font-medium">Outstanding</th>
                        <th className="px-4 py-3 text-center font-medium">Status</th>
                        <th className="px-4 py-3 text-center font-medium">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {schedule.map((line) => (
                        <tr key={line.id} className="border-b">
                          <td className="px-4 py-3">{line.installment_no}</td>
                          <td className="px-4 py-3 text-muted-foreground">
                            {line.period_start} — {line.period_end}
                          </td>
                          <td className="px-4 py-3">{line.due_date}</td>
                          <td className="px-4 py-3 text-right">
                            {Number(line.base_rent).toLocaleString()}
                          </td>
                          <td className="px-4 py-3 text-right font-medium">
                            {Number(line.total_due).toLocaleString()}
                          </td>
                          <td className="px-4 py-3 text-right text-green-600">
                            {Number(line.total_allocated).toLocaleString()}
                          </td>
                          <td className="px-4 py-3 text-right text-red-600">
                            {Number(line.outstanding).toLocaleString()}
                          </td>
                          <td className="px-4 py-3 text-center">
                            <Badge className={cn("text-xs", SCHEDULE_STATUS_COLORS[line.status])}>
                              {line.status}
                            </Badge>
                          </td>
                          <td className="px-4 py-3 text-center">
                            {(line.status === "due" || line.status === "overdue") && (
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-7 text-xs"
                                onClick={() => {
                                  setWaiveLineId(line.id);
                                  setWaiveOpen(true);
                                }}
                              >
                                <Ban className="mr-1 h-3 w-3" />
                                Waive
                              </Button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </TabsContent>

          <TabsContent value="payments" className="mt-4">
            <div className="space-y-4">
              {lease.status === "active" && (
                <div className="flex justify-end">
                  <Button size="sm" onClick={() => setPaymentOpen(true)}>
                    <DollarSign className="mr-1 h-4 w-4" />
                    Record Payment
                  </Button>
                </div>
              )}
              {paymentsLoading ? (
                <LoadingSpinner />
              ) : !payments?.length ? (
                <Card>
                  <CardContent className="py-8 text-center text-muted-foreground">
                    No payments recorded for this lease.
                  </CardContent>
                </Card>
              ) : (
                <div className="rounded-lg border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b bg-muted/50">
                        <th className="px-4 py-3 text-left font-medium">Receipt #</th>
                        <th className="px-4 py-3 text-left font-medium">Date</th>
                        <th className="px-4 py-3 text-right font-medium">Amount</th>
                        <th className="px-4 py-3 text-left font-medium">Method</th>
                        <th className="px-4 py-3 text-left font-medium">Reference</th>
                        <th className="px-4 py-3 text-center font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {payments.map((p) => (
                        <tr key={p.id} className={cn("border-b", p.voided && "opacity-50")}>
                          <td className="px-4 py-3 font-medium">{p.receipt_no}</td>
                          <td className="px-4 py-3">{p.payment_date}</td>
                          <td className="px-4 py-3 text-right">
                            {Number(p.amount).toLocaleString()} {p.currency}
                          </td>
                          <td className="px-4 py-3">{PAYMENT_METHOD_LABELS[p.method] || p.method}</td>
                          <td className="px-4 py-3 text-muted-foreground">{p.reference_no || "—"}</td>
                          <td className="px-4 py-3 text-center">
                            {p.voided ? (
                              <Badge className="text-xs bg-red-100 text-red-800">Voided</Badge>
                            ) : (
                              <Badge className={cn("text-xs", ALLOCATION_STATUS_COLORS[p.allocation_status])}>
                                {p.allocation_status.replace("_", " ")}
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
          </TabsContent>

          <TabsContent value="deposits" className="mt-4">
            <div className="space-y-4">
              {lease.status !== "draft" && (
                <div className="flex justify-end">
                  <Button size="sm" onClick={() => setDepositOpen(true)}>
                    <Shield className="mr-1 h-4 w-4" />
                    Record Deposit
                  </Button>
                </div>
              )}
              {depositsLoading ? (
                <LoadingSpinner />
              ) : !deposits?.length ? (
                <Card>
                  <CardContent className="py-8 text-center text-muted-foreground">
                    No deposit transactions recorded for this lease.
                  </CardContent>
                </Card>
              ) : (
                <div className="space-y-4">
                  <Card>
                    <CardContent className="pt-4">
                      <div className="text-sm text-muted-foreground">Deposit Balance</div>
                      <div className="text-xl font-bold">
                        {deposits.reduce((sum, d) => {
                          const amt = Number(d.amount);
                          return sum + (d.transaction_type === "received" || d.transaction_type === "adjusted" ? amt : -amt);
                        }, 0).toLocaleString()}{" "}
                        {lease.currency}
                      </div>
                    </CardContent>
                  </Card>
                  <div className="rounded-lg border">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b bg-muted/50">
                          <th className="px-4 py-3 text-left font-medium">Date</th>
                          <th className="px-4 py-3 text-center font-medium">Type</th>
                          <th className="px-4 py-3 text-right font-medium">Amount</th>
                          <th className="px-4 py-3 text-left font-medium">Reason</th>
                          <th className="px-4 py-3 text-left font-medium">Reference</th>
                        </tr>
                      </thead>
                      <tbody>
                        {deposits.map((d) => (
                          <tr key={d.id} className="border-b">
                            <td className="px-4 py-3">{d.transaction_date}</td>
                            <td className="px-4 py-3 text-center">
                              <Badge className={cn("text-xs", DEPOSIT_TYPE_COLORS[d.transaction_type])}>
                                {d.transaction_type}
                              </Badge>
                            </td>
                            <td className="px-4 py-3 text-right">
                              {Number(d.amount).toLocaleString()} {d.currency}
                            </td>
                            <td className="px-4 py-3 text-muted-foreground">{d.reason || "—"}</td>
                            <td className="px-4 py-3 text-muted-foreground">{d.reference || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </TabsContent>
        </Tabs>
      </div>

      {/* Terminate Dialog */}
      <Dialog open={terminateOpen} onOpenChange={setTerminateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Terminate Lease</DialogTitle>
            <DialogDescription>
              This will terminate the lease and set the unit back to vacant.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="termination_reason">Reason</Label>
            <Textarea
              id="termination_reason"
              value={terminationReason}
              onChange={(e) => setTerminationReason(e.target.value)}
              placeholder="Enter the reason for termination..."
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTerminateOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleTerminate}
              disabled={!terminationReason.trim() || terminateLease.isPending}
            >
              {terminateLease.isPending ? "Terminating..." : "Terminate"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Renew Dialog */}
      <Dialog open={renewOpen} onOpenChange={setRenewOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Renew Lease</DialogTitle>
            <DialogDescription>
              Creates a new draft lease linked to the current one. The current lease will be marked as renewed.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="new_contract_no">New Contract No</Label>
              <Input
                id="new_contract_no"
                value={renewForm.new_contract_no}
                onChange={(e) => setRenewForm({ ...renewForm, new_contract_no: e.target.value })}
                placeholder="e.g., LC-2026-002"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="new_start_date">Start Date</Label>
                <Input
                  id="new_start_date"
                  type="date"
                  value={renewForm.new_start_date}
                  onChange={(e) => setRenewForm({ ...renewForm, new_start_date: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new_end_date">End Date</Label>
                <Input
                  id="new_end_date"
                  type="date"
                  value={renewForm.new_end_date}
                  onChange={(e) => setRenewForm({ ...renewForm, new_end_date: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="new_rent_amount">New Rent Amount (optional)</Label>
              <Input
                id="new_rent_amount"
                type="number"
                value={renewForm.new_rent_amount}
                onChange={(e) => setRenewForm({ ...renewForm, new_rent_amount: e.target.value })}
                placeholder={`Current: ${Number(lease.rent_amount).toLocaleString()}`}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenewOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleRenew}
              disabled={
                !renewForm.new_contract_no || !renewForm.new_start_date || !renewForm.new_end_date || renewLease.isPending
              }
            >
              {renewLease.isPending ? "Renewing..." : "Renew Lease"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Waive Dialog */}
      <Dialog open={waiveOpen} onOpenChange={setWaiveOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Waive Schedule Line</DialogTitle>
            <DialogDescription>
              This will mark the schedule line as waived and set its outstanding amount to zero.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="waive_reason">Reason</Label>
            <Textarea
              id="waive_reason"
              value={waiveReason}
              onChange={(e) => setWaiveReason(e.target.value)}
              placeholder="Enter the reason for waiving..."
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setWaiveOpen(false)}>Cancel</Button>
            <Button
              onClick={handleWaive}
              disabled={!waiveReason.trim() || waiveScheduleLine.isPending}
            >
              {waiveScheduleLine.isPending ? "Waiving..." : "Waive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Record Payment Dialog */}
      <Dialog open={paymentOpen} onOpenChange={setPaymentOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Record Payment</DialogTitle>
            <DialogDescription>
              Record a rent payment for this lease.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="receipt_no">Receipt No</Label>
              <Input
                id="receipt_no"
                value={paymentForm.receipt_no}
                onChange={(e) => setPaymentForm({ ...paymentForm, receipt_no: e.target.value })}
                placeholder="e.g., RCP-001"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="payment_amount">Amount</Label>
                <Input
                  id="payment_amount"
                  type="number"
                  value={paymentForm.amount}
                  onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="payment_date">Date</Label>
                <Input
                  id="payment_date"
                  type="date"
                  value={paymentForm.payment_date}
                  onChange={(e) => setPaymentForm({ ...paymentForm, payment_date: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="payment_method">Method</Label>
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
              <Label htmlFor="payment_ref">Reference No (optional)</Label>
              <Input
                id="payment_ref"
                value={paymentForm.reference_no}
                onChange={(e) => setPaymentForm({ ...paymentForm, reference_no: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPaymentOpen(false)}>Cancel</Button>
            <Button
              onClick={handleCreatePayment}
              disabled={!paymentForm.receipt_no || !paymentForm.amount || !paymentForm.payment_date || createPayment.isPending}
            >
              {createPayment.isPending ? "Saving..." : "Record Payment"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Record Deposit Dialog */}
      <Dialog open={depositOpen} onOpenChange={setDepositOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Record Deposit Transaction</DialogTitle>
            <DialogDescription>
              Record a security deposit transaction for this lease.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="deposit_type">Transaction Type</Label>
              <Select
                value={depositForm.transaction_type}
                onValueChange={(v) => setDepositForm({ ...depositForm, transaction_type: v as DepositTransactionType })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="received">Received</SelectItem>
                  <SelectItem value="adjusted">Adjusted</SelectItem>
                  <SelectItem value="refunded">Refunded</SelectItem>
                  <SelectItem value="forfeited">Forfeited</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="deposit_amount">Amount</Label>
                <Input
                  id="deposit_amount"
                  type="number"
                  value={depositForm.amount}
                  onChange={(e) => setDepositForm({ ...depositForm, amount: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="deposit_date">Date</Label>
                <Input
                  id="deposit_date"
                  type="date"
                  value={depositForm.transaction_date}
                  onChange={(e) => setDepositForm({ ...depositForm, transaction_date: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="deposit_reason">Reason (optional)</Label>
              <Input
                id="deposit_reason"
                value={depositForm.reason}
                onChange={(e) => setDepositForm({ ...depositForm, reason: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="deposit_reference">Reference (optional)</Label>
              <Input
                id="deposit_reference"
                value={depositForm.reference}
                onChange={(e) => setDepositForm({ ...depositForm, reference: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDepositOpen(false)}>Cancel</Button>
            <Button
              onClick={handleCreateDeposit}
              disabled={!depositForm.amount || !depositForm.transaction_date || createDeposit.isPending}
            >
              {createDeposit.isPending ? "Saving..." : "Record"}
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
