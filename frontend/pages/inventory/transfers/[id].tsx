import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Send, XCircle, Info } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import {
  useInventoryTransfer,
  usePostInventoryTransfer,
  useVoidInventoryTransfer,
} from "@/queries/useInventory";
import { useToast } from "@/components/ui/toaster";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import { TRANSFER_STATUS_COLORS, TRANSFER_STATUS_LABELS } from "@/types/inventory";
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

export default function InventoryTransferDetailPage() {
  const router = useRouter();
  const { id } = router.query;
  const { toast } = useToast();
  const { formatDate } = useCompanyFormat();

  const { data: transfer, isLoading } = useInventoryTransfer(parseInt(id as string));
  const postTransfer = usePostInventoryTransfer();
  const voidTransfer = useVoidInventoryTransfer();

  const [postDialog, setPostDialog] = useState(false);
  const [voidDialog, setVoidDialog] = useState(false);

  const errMsg = (error: any, fallback: string) => {
    const body = error?.response?.data;
    if (body?.detail) return body.detail;
    return fallback;
  };

  const handlePost = async () => {
    if (!transfer) return;
    try {
      await postTransfer.mutateAsync(transfer.id);
      toast({
        title: "Transfer posted",
        description: `${transfer.transfer_number} posted. Stock moved.`,
      });
    } catch (error: any) {
      toast({ title: "Error", description: errMsg(error, "Failed to post."), variant: "destructive" });
    } finally {
      setPostDialog(false);
    }
  };

  const handleVoid = async () => {
    if (!transfer) return;
    try {
      await voidTransfer.mutateAsync(transfer.id);
      toast({ title: "Transfer voided", description: `${transfer.transfer_number} reversed.` });
    } catch (error: any) {
      toast({ title: "Error", description: errMsg(error, "Failed to void."), variant: "destructive" });
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

  if (!transfer) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <p className="text-muted-foreground">Transfer not found</p>
          <Link href="/inventory/transfers">
            <Button variant="link">Back to transfers</Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={
            <div className="flex items-center gap-3">
              <span>{transfer.transfer_number}</span>
              <Badge className={cn("text-sm", TRANSFER_STATUS_COLORS[transfer.status])}>
                {TRANSFER_STATUS_LABELS[transfer.status]}
              </Badge>
            </div>
          }
          subtitle={`${transfer.source_warehouse_code} → ${transfer.destination_warehouse_code}`}
          actions={
            <div className="flex gap-2 flex-wrap">
              <Link href="/inventory/transfers">
                <Button variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />Back
                </Button>
              </Link>
              {transfer.status === "DRAFT" && (
                <Button onClick={() => setPostDialog(true)}>
                  <Send className="h-4 w-4 me-2" />Post Transfer
                </Button>
              )}
              {transfer.status === "POSTED" && (
                <Button variant="destructive" onClick={() => setVoidDialog(true)}>
                  <XCircle className="h-4 w-4 me-2" />Void
                </Button>
              )}
            </div>
          }
        />

        <Card>
          <CardHeader><CardTitle>Transfer Details</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-sm text-muted-foreground">Transfer Date</p>
                <p className="font-medium">{formatDate(transfer.transfer_date)}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">From</p>
                <p className="font-medium font-mono">{transfer.source_warehouse_code}</p>
                <p className="text-xs text-muted-foreground">{transfer.source_warehouse_name}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">To</p>
                <p className="font-medium font-mono">{transfer.destination_warehouse_code}</p>
                <p className="text-xs text-muted-foreground">{transfer.destination_warehouse_name}</p>
              </div>
              {transfer.posted_at && (
                <div>
                  <p className="text-sm text-muted-foreground">Posted At</p>
                  <p className="font-medium">{formatDate(transfer.posted_at)}</p>
                </div>
              )}
            </div>

            <div className="mt-4 p-3 bg-muted rounded-lg flex items-start gap-2">
              <Info className="h-4 w-4 text-muted-foreground mt-0.5 flex-shrink-0" />
              <p className="text-sm text-muted-foreground">
                Posting issues stock from {transfer.source_warehouse_code} at its current moving-average cost
                and receives it into {transfer.destination_warehouse_code} at the same unit cost. No journal
                entry — both legs hit the same Inventory GL account so the net is zero. The stock ledger
                captures the two movements.
              </p>
            </div>

            {transfer.notes && (
              <div className="mt-4">
                <p className="text-sm text-muted-foreground">Notes</p>
                <p className="text-sm mt-1 whitespace-pre-wrap">{transfer.notes}</p>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Items</CardTitle></CardHeader>
          <CardContent>
            <table className="w-full">
              <thead>
                <tr className="border-b text-sm text-muted-foreground">
                  <th className="text-start py-2 px-2 w-[60px]">#</th>
                  <th className="text-start py-2 px-2">Item</th>
                  <th className="text-end py-2 px-2 w-[120px]">Qty</th>
                  <th className="text-end py-2 px-2 w-[140px]">Unit Cost</th>
                  <th className="text-end py-2 px-2 w-[140px]">Line Cost</th>
                </tr>
              </thead>
              <tbody>
                {transfer.lines.map((line) => {
                  const qty = parseFloat(line.qty) || 0;
                  const unit = parseFloat(line.unit_cost_snapshot) || 0;
                  return (
                    <tr key={line.id} className="border-b">
                      <td className="py-2 px-2 text-muted-foreground">{line.line_number}</td>
                      <td className="py-2 px-2">
                        <p className="font-medium">{line.item_name}</p>
                        <p className="text-xs text-muted-foreground font-mono">{line.item_code}</p>
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm">{qty.toLocaleString()}</td>
                      <td className="py-2 px-2 text-end font-mono text-sm">
                        {unit.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 })}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm font-medium">
                        {(qty * unit).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={postDialog} onOpenChange={setPostDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Post Inventory Transfer</AlertDialogTitle>
            <AlertDialogDescription>
              Post &quot;{transfer.transfer_number}&quot;? Stock will move from{" "}
              {transfer.source_warehouse_code} to {transfer.destination_warehouse_code}. Each line
              moves at the source warehouse&apos;s current moving-average cost.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePost}>Post Transfer</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={voidDialog} onOpenChange={setVoidDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Void Transfer</AlertDialogTitle>
            <AlertDialogDescription>
              Reverse &quot;{transfer.transfer_number}&quot;? Stock will move back from{" "}
              {transfer.destination_warehouse_code} to {transfer.source_warehouse_code} at the
              snapshot cost captured at posting.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleVoid}
              className="bg-destructive text-destructive-foreground"
            >
              Void Transfer
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
