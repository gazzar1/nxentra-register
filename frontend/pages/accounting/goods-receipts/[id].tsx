import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Send, XCircle, Info, FileText } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useGoodsReceipt, usePostGoodsReceipt, useVoidGoodsReceipt } from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import { GR_STATUS_COLORS, GR_STATUS_LABELS } from "@/types/purchases";
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

export default function GoodsReceiptDetailPage() {
  const router = useRouter();
  const { id } = router.query;
  const { toast } = useToast();
  const { formatDate } = useCompanyFormat();

  const { data: gr, isLoading } = useGoodsReceipt(parseInt(id as string));
  const postGR = usePostGoodsReceipt();
  const voidGR = useVoidGoodsReceipt();

  const [postDialog, setPostDialog] = useState(false);
  const [voidDialog, setVoidDialog] = useState(false);

  const errMsg = (error: any, fallback: string) => {
    const body = error?.response?.data;
    if (body?.detail) return body.detail;
    if (body && typeof body === "object") {
      const parts = Object.entries(body).map(([f, m]) => `${f}: ${Array.isArray(m) ? m.join("; ") : String(m)}`);
      if (parts.length) return parts.join(" | ");
    }
    return fallback;
  };

  const handlePost = async () => {
    if (!gr) return;
    try {
      await postGR.mutateAsync(gr.id);
      toast({
        title: "Goods receipt posted",
        description: `${gr.receipt_number} posted. Stock and moving-average cost updated.`,
      });
    } catch (error: any) {
      toast({ title: "Error", description: errMsg(error, "Failed to post receipt."), variant: "destructive" });
    } finally {
      setPostDialog(false);
    }
  };

  const handleVoid = async () => {
    if (!gr) return;
    try {
      await voidGR.mutateAsync({ id: gr.id });
      toast({ title: "Goods receipt voided", description: `${gr.receipt_number} has been voided.` });
    } catch (error: any) {
      toast({ title: "Error", description: errMsg(error, "Failed to void receipt."), variant: "destructive" });
    } finally {
      setVoidDialog(false);
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <LoadingSpinner />
      </AppLayout>
    );
  }

  if (!gr) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <p className="text-muted-foreground">Goods receipt not found</p>
          <Link href="/accounting/goods-receipts">
            <Button variant="link">Back to goods receipts</Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  const fmt = (s: string) =>
    parseFloat(s).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={
            <div className="flex items-center gap-3">
              <span>{gr.receipt_number}</span>
              <Badge className={cn("text-sm", GR_STATUS_COLORS[gr.status])}>{GR_STATUS_LABELS[gr.status]}</Badge>
            </div>
          }
          subtitle={`Goods received from ${gr.vendor_name} into ${gr.warehouse_name}`}
          actions={
            <div className="flex gap-2 flex-wrap">
              <Link href="/accounting/goods-receipts">
                <Button variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />Back
                </Button>
              </Link>
              {gr.status === "DRAFT" && (
                <>
                  <Button onClick={() => setPostDialog(true)}>
                    <Send className="h-4 w-4 me-2" />Post Receipt
                  </Button>
                  <Button variant="destructive" onClick={() => setVoidDialog(true)}>
                    <XCircle className="h-4 w-4 me-2" />Void
                  </Button>
                </>
              )}
              {gr.status === "POSTED" && (
                <Link href={`/accounting/purchase-bills/new?po=${gr.purchase_order}`}>
                  <Button>
                    <FileText className="h-4 w-4 me-2" />Create Vendor Bill
                  </Button>
                </Link>
              )}
            </div>
          }
        />

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Receipt Details</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Receipt Date</p>
                  <p className="font-medium">{formatDate(gr.receipt_date)}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Purchase Order</p>
                  <Link
                    href={`/accounting/purchase-orders/${gr.purchase_order}`}
                    className="font-mono text-primary hover:underline"
                  >
                    {gr.order_number}
                  </Link>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Warehouse</p>
                  <p className="font-medium">{gr.warehouse_name}</p>
                </div>
                {gr.posted_at && (
                  <div>
                    <p className="text-sm text-muted-foreground">Posted At</p>
                    <p className="font-medium">{formatDate(gr.posted_at)}</p>
                  </div>
                )}
              </div>

              <div className="mt-4 p-3 bg-muted rounded-lg flex items-start gap-2">
                <Info className="h-4 w-4 text-muted-foreground mt-0.5 flex-shrink-0" />
                <p className="text-sm text-muted-foreground">
                  Posting a goods receipt updates stock on hand and moving-average cost per item — but does
                  not create a journal entry. The JE (Dr Inventory/Expense / Cr AP) posts when you record the
                  vendor bill against this PO.
                </p>
              </div>

              {gr.notes && (
                <div className="mt-4">
                  <p className="text-sm text-muted-foreground">Notes</p>
                  <p className="text-sm mt-1 whitespace-pre-wrap">{gr.notes}</p>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Vendor</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="font-medium">{gr.vendor_name}</p>
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
                    <th className="text-start py-2 px-2 w-[80px]">PO Line</th>
                    <th className="text-start py-2 px-2">Description</th>
                    <th className="text-end py-2 px-2 w-[100px]">Qty Received</th>
                    <th className="text-end py-2 px-2 w-[100px]">Unit Cost</th>
                    <th className="text-end py-2 px-2 w-[120px]">Line Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {gr.lines.map((line) => {
                    const qty = parseFloat(line.qty_received) || 0;
                    const unit = parseFloat(line.unit_cost) || 0;
                    return (
                      <tr key={line.id} className="border-b">
                        <td className="py-2 px-2 text-muted-foreground">{line.line_number}</td>
                        <td className="py-2 px-2 text-muted-foreground font-mono text-sm">{line.po_line_number}</td>
                        <td className="py-2 px-2">
                          <p className="font-medium">{line.description}</p>
                        </td>
                        <td className="py-2 px-2 text-end font-mono text-sm">{qty.toLocaleString()}</td>
                        <td className="py-2 px-2 text-end font-mono text-sm">{fmt(line.unit_cost)}</td>
                        <td className="py-2 px-2 text-end font-mono text-sm font-medium">{fmt(String(qty * unit))}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={postDialog} onOpenChange={setPostDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Post Goods Receipt</AlertDialogTitle>
            <AlertDialogDescription>
              Post &quot;{gr.receipt_number}&quot;? Stock on hand and moving-average cost will update. No journal
              entry is created — that happens at vendor bill posting.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePost}>Post Receipt</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={voidDialog} onOpenChange={setVoidDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Void Goods Receipt</AlertDialogTitle>
            <AlertDialogDescription>
              Void &quot;{gr.receipt_number}&quot;? PO received quantities will be rolled back.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep</AlertDialogCancel>
            <AlertDialogAction onClick={handleVoid} className="bg-destructive text-destructive-foreground">
              Void Receipt
            </AlertDialogAction>
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
