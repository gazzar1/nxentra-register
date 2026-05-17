import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, CheckCircle, XCircle, Lock, FileText, Info } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import {
  usePurchaseOrder,
  useApprovePurchaseOrder,
  useCancelPurchaseOrder,
  useClosePurchaseOrder,
  useCreateBillFromPO,
} from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import { PO_STATUS_COLORS, PO_STATUS_LABELS } from "@/types/purchases";
import { cn } from "@/lib/cn";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export default function PurchaseOrderDetailPage() {
  const router = useRouter();
  const { id } = router.query;
  const { toast } = useToast();
  const { formatDate } = useCompanyFormat();

  const { data: po, isLoading } = usePurchaseOrder(parseInt(id as string));
  const approvePO = useApprovePurchaseOrder();
  const cancelPO = useCancelPurchaseOrder();
  const closePO = useClosePurchaseOrder();
  const createBill = useCreateBillFromPO();

  const [approveDialog, setApproveDialog] = useState(false);
  const [cancelDialog, setCancelDialog] = useState(false);
  const [closeDialog, setCloseDialog] = useState(false);
  const [billDialog, setBillDialog] = useState(false);

  const errMsg = (error: any, fallback: string) => {
    const body = error?.response?.data;
    if (body?.detail) return body.detail;
    if (body && typeof body === "object") {
      const parts = Object.entries(body).map(([f, m]) => `${f}: ${Array.isArray(m) ? m.join("; ") : String(m)}`);
      if (parts.length) return parts.join(" | ");
    }
    return fallback;
  };

  const handleApprove = async () => {
    if (!po) return;
    try {
      await approvePO.mutateAsync(po.id);
      toast({ title: "PO approved", description: `${po.order_number} can now receive goods.` });
    } catch (error: any) {
      toast({ title: "Error", description: errMsg(error, "Failed to approve PO."), variant: "destructive" });
    } finally {
      setApproveDialog(false);
    }
  };

  const handleCancel = async () => {
    if (!po) return;
    try {
      await cancelPO.mutateAsync({ id: po.id });
      toast({ title: "PO cancelled", description: `${po.order_number} has been cancelled.` });
    } catch (error: any) {
      toast({ title: "Error", description: errMsg(error, "Failed to cancel PO."), variant: "destructive" });
    } finally {
      setCancelDialog(false);
    }
  };

  const handleClose = async () => {
    if (!po) return;
    try {
      await closePO.mutateAsync(po.id);
      toast({ title: "PO closed", description: `${po.order_number} closed. Undelivered quantities abandoned.` });
    } catch (error: any) {
      toast({ title: "Error", description: errMsg(error, "Failed to close PO."), variant: "destructive" });
    } finally {
      setCloseDialog(false);
    }
  };

  const handleCreateBill = async () => {
    if (!po) return;
    try {
      const result = await createBill.mutateAsync({ id: po.id });
      toast({ title: "Bill created", description: `Vendor bill drafted from ${po.order_number}.` });
      const newBillId = (result as any)?.data?.id;
      if (newBillId) router.push(`/accounting/purchase-bills/${newBillId}`);
    } catch (error: any) {
      toast({ title: "Error", description: errMsg(error, "Failed to create bill."), variant: "destructive" });
    } finally {
      setBillDialog(false);
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <LoadingSpinner />
      </AppLayout>
    );
  }

  if (!po) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <p className="text-muted-foreground">Purchase order not found</p>
          <Link href="/accounting/purchase-orders">
            <Button variant="link">Back to purchase orders</Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  const canApprove = po.status === "DRAFT";
  const canCancel = po.status === "DRAFT" || po.status === "APPROVED";
  const canReceive = po.status === "APPROVED" || po.status === "PARTIALLY_RECEIVED";
  const canClose = po.status === "PARTIALLY_RECEIVED" || po.status === "FULLY_RECEIVED";
  const canCreateBill = po.status === "APPROVED" || po.status === "PARTIALLY_RECEIVED" || po.status === "FULLY_RECEIVED";

  const fmt = (s: string) =>
    parseFloat(s).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={
            <div className="flex items-center gap-3">
              <span>{po.order_number}</span>
              <Badge className={cn("text-sm", PO_STATUS_COLORS[po.status])}>{PO_STATUS_LABELS[po.status]}</Badge>
            </div>
          }
          subtitle={`Purchase order to ${po.vendor_name}`}
          actions={
            <div className="flex gap-2 flex-wrap">
              <Link href="/accounting/purchase-orders">
                <Button variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />Back
                </Button>
              </Link>
              {canApprove && (
                <Button onClick={() => setApproveDialog(true)}>
                  <CheckCircle className="h-4 w-4 me-2" />Approve
                </Button>
              )}
              {canReceive && (
                <Link href={`/accounting/goods-receipts/new?po=${po.id}`}>
                  <Button>
                    <FileText className="h-4 w-4 me-2" />Receive Goods
                  </Button>
                </Link>
              )}
              {canCreateBill && (
                <Button variant="outline" onClick={() => setBillDialog(true)}>
                  <FileText className="h-4 w-4 me-2" />Create Bill
                </Button>
              )}
              {canClose && (
                <Button variant="outline" onClick={() => setCloseDialog(true)}>
                  <Lock className="h-4 w-4 me-2" />Close PO
                </Button>
              )}
              {canCancel && (
                <Button variant="destructive" onClick={() => setCancelDialog(true)}>
                  <XCircle className="h-4 w-4 me-2" />Cancel
                </Button>
              )}
            </div>
          }
        />

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Order Details</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Order Date</p>
                  <p className="font-medium">{formatDate(po.order_date)}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Expected Delivery</p>
                  <p className="font-medium">
                    {po.expected_delivery_date ? formatDate(po.expected_delivery_date) : "—"}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Reference</p>
                  <p className="font-medium">{po.reference || "—"}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Currency</p>
                  <p className="font-medium font-mono">{po.currency}</p>
                </div>
                {po.exchange_rate && parseFloat(po.exchange_rate) !== 1 && (
                  <div>
                    <p className="text-sm text-muted-foreground">Exchange Rate</p>
                    <p className="font-medium font-mono">{parseFloat(po.exchange_rate).toFixed(6)}</p>
                  </div>
                )}
                {po.approved_at && (
                  <div>
                    <p className="text-sm text-muted-foreground">Approved At</p>
                    <p className="font-medium">{formatDate(po.approved_at)}</p>
                  </div>
                )}
              </div>

              <div className="mt-4 p-3 bg-muted rounded-lg flex items-start gap-2">
                <Info className="h-4 w-4 text-muted-foreground mt-0.5 flex-shrink-0" />
                <p className="text-sm text-muted-foreground">
                  A purchase order is a commitment, not a financial event — no journal entry is posted here.
                  The JE is created when the vendor bill is posted (Dr Inventory/Expense / Cr AP). Stock and
                  moving-average cost update at goods receipt.
                </p>
              </div>

              {po.notes && (
                <div className="mt-4">
                  <p className="text-sm text-muted-foreground">Notes</p>
                  <p className="text-sm mt-1 whitespace-pre-wrap">{po.notes}</p>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Vendor</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="font-medium">{po.vendor_name}</p>
              <p className="text-sm text-muted-foreground font-mono">{po.vendor_code}</p>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Line Items</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b text-sm text-muted-foreground">
                    <th className="text-start py-2 px-2 w-[50px]">#</th>
                    <th className="text-start py-2 px-2">Description</th>
                    <th className="text-start py-2 px-2 w-[220px]">Account</th>
                    <th className="text-end py-2 px-2 w-[80px]">Qty</th>
                    <th className="text-end py-2 px-2 w-[80px]">Received</th>
                    <th className="text-end py-2 px-2 w-[80px]">Billed</th>
                    <th className="text-end py-2 px-2 w-[100px]">Unit Price</th>
                    <th className="text-end py-2 px-2 w-[100px]">Net</th>
                    <th className="text-end py-2 px-2 w-[80px]">Tax</th>
                    <th className="text-end py-2 px-2 w-[100px]">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {po.lines.map((line) => (
                    <tr key={line.id} className="border-b">
                      <td className="py-2 px-2 text-muted-foreground">{line.line_number}</td>
                      <td className="py-2 px-2">
                        <p className="font-medium">{line.description}</p>
                      </td>
                      <td className="py-2 px-2 text-sm">
                        <span className="font-mono">{line.account_code}</span>
                        {line.account_name && <span className="text-muted-foreground"> - {line.account_name}</span>}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm">
                        {parseFloat(line.quantity).toLocaleString()}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm text-muted-foreground">
                        {parseFloat(line.qty_received).toLocaleString()}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm text-muted-foreground">
                        {parseFloat(line.qty_billed).toLocaleString()}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm">{fmt(line.unit_price)}</td>
                      <td className="py-2 px-2 text-end font-mono text-sm">{fmt(line.net_amount)}</td>
                      <td className="py-2 px-2 text-end font-mono text-sm">{fmt(line.tax_amount)}</td>
                      <td className="py-2 px-2 text-end font-mono text-sm font-medium">{fmt(line.line_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex justify-end mt-6">
              <div className="w-80 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Subtotal</span>
                  <span className="font-mono">
                    <span className="text-muted-foreground me-1">{po.currency}</span>
                    {fmt(po.subtotal)}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Total Discount</span>
                  <span className="font-mono text-red-600">-{fmt(po.total_discount)}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Total Tax</span>
                  <span className="font-mono">{fmt(po.total_tax)}</span>
                </div>
                <div className="border-t pt-2 flex justify-between text-lg font-semibold">
                  <span>Total Amount</span>
                  <span className="font-mono">
                    <span className="text-muted-foreground text-sm me-1">{po.currency}</span>
                    {fmt(po.total_amount)}
                  </span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={approveDialog} onOpenChange={setApproveDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Approve Purchase Order</AlertDialogTitle>
            <AlertDialogDescription>
              Approve &quot;{po.order_number}&quot;? This will allow goods to be received against it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleApprove}>Approve</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={cancelDialog} onOpenChange={setCancelDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel Purchase Order</AlertDialogTitle>
            <AlertDialogDescription>
              Cancel &quot;{po.order_number}&quot;? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep</AlertDialogCancel>
            <AlertDialogAction onClick={handleCancel} className="bg-destructive text-destructive-foreground">
              Cancel PO
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={closeDialog} onOpenChange={setCloseDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Close Purchase Order</AlertDialogTitle>
            <AlertDialogDescription>
              Close &quot;{po.order_number}&quot;? Any remaining undelivered quantities will be abandoned.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep Open</AlertDialogCancel>
            <AlertDialogAction onClick={handleClose}>Close PO</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={billDialog} onOpenChange={setBillDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Create Vendor Bill</AlertDialogTitle>
            <AlertDialogDescription>
              Draft a vendor bill from the unbilled lines on &quot;{po.order_number}&quot;? You can edit
              it before posting; the JE is created at bill posting.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleCreateBill}>Create Bill</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])),
    },
  };
};
