import React from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Eye, Send, XCircle, MoreHorizontal, Package } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState } from "@/components/common";
import { PaginatedTable } from "@/components/common/PaginatedTable";
import type { ColumnDef } from "@/components/common/PaginatedTable";
import {
  usePaginatedGoodsReceipts,
  usePostGoodsReceipt,
  useVoidGoodsReceipt,
} from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import type { GoodsReceiptListItem } from "@/types/purchases";
import { GR_STATUS_COLORS, GR_STATUS_LABELS } from "@/types/purchases";
import { cn } from "@/lib/cn";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export default function GoodsReceiptsPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { formatDate } = useCompanyFormat();
  const postGR = usePostGoodsReceipt();
  const voidGR = useVoidGoodsReceipt();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("-receipt_date");
  const [postDialog, setPostDialog] = useState<{ open: boolean; gr: GoodsReceiptListItem | null }>({ open: false, gr: null });
  const [voidDialog, setVoidDialog] = useState<{ open: boolean; gr: GoodsReceiptListItem | null }>({ open: false, gr: null });

  const { data: response, isLoading } = usePaginatedGoodsReceipts({
    page, page_size: pageSize, ordering,
  });

  const receipts = response?.results || [];
  const totalCount = response?.count || 0;
  const totalPages = response?.total_pages || 1;

  const handlePost = async () => {
    if (!postDialog.gr) return;
    try {
      await postGR.mutateAsync(postDialog.gr.id);
      toast({ title: "Receipt posted", description: `${postDialog.gr.receipt_number} has been posted. Stock updated.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to post receipt.", variant: "destructive" });
    } finally {
      setPostDialog({ open: false, gr: null });
    }
  };

  const handleVoid = async () => {
    if (!voidDialog.gr) return;
    try {
      await voidGR.mutateAsync({ id: voidDialog.gr.id });
      toast({ title: "Receipt voided", description: `${voidDialog.gr.receipt_number} has been voided. Stock reversed.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to void receipt.", variant: "destructive" });
    } finally {
      setVoidDialog({ open: false, gr: null });
    }
  };

  const columns: ColumnDef<GoodsReceiptListItem>[] = [
    {
      key: "receipt_number",
      label: "GRN #",
      sortable: true,
      render: (gr) => (
        <Link href={`/accounting/goods-receipts/${gr.id}`} className="font-mono text-sm font-medium hover:text-primary hover:underline ltr-code">
          {gr.receipt_number}
        </Link>
      ),
    },
    {
      key: "receipt_date",
      label: "Date",
      sortable: true,
      render: (gr) => <span className="text-sm text-muted-foreground">{formatDate(gr.receipt_date)}</span>,
    },
    {
      key: "order_number",
      label: "Purchase Order",
      render: (gr) => (
        <Link href={`/accounting/purchase-orders/${gr.purchase_order}`} className="font-mono text-sm hover:text-primary hover:underline ltr-code">
          {gr.order_number}
        </Link>
      ),
    },
    {
      key: "vendor_name",
      label: "Vendor",
      render: (gr) => <span className="font-medium">{gr.vendor_name}</span>,
    },
    {
      key: "warehouse_name",
      label: "Warehouse",
      render: (gr) => <span className="text-sm">{gr.warehouse_name}</span>,
    },
    {
      key: "status",
      label: "Status",
      sortable: true,
      render: (gr) => <Badge className={cn("text-xs", GR_STATUS_COLORS[gr.status])}>{GR_STATUS_LABELS[gr.status]}</Badge>,
    },
    {
      key: "actions",
      label: "",
      render: (gr) => (
        <div className="flex items-center justify-end" onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm"><MoreHorizontal className="h-4 w-4" /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => router.push(`/accounting/goods-receipts/${gr.id}`)}>
                <Eye className="h-4 w-4 me-2" />View
              </DropdownMenuItem>
              {gr.status === "DRAFT" && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setPostDialog({ open: true, gr })}>
                    <Send className="h-4 w-4 me-2" />Post Receipt
                  </DropdownMenuItem>
                </>
              )}
              {gr.status === "POSTED" && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setVoidDialog({ open: true, gr })} className="text-destructive">
                    <XCircle className="h-4 w-4 me-2" />Void Receipt
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Goods Receipts"
          subtitle="Track goods received against purchase orders"
          actions={
            <Link href="/accounting/goods-receipts/new">
              <Button><Plus className="h-4 w-4 me-2" />New Receipt</Button>
            </Link>
          }
        />
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input placeholder="Search receipts..." value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} className="ps-10" />
              </div>
            </div>
            <PaginatedTable
              data={receipts}
              columns={columns}
              keyExtractor={(gr) => gr.id}
              page={page} pageSize={pageSize} totalCount={totalCount} totalPages={totalPages}
              onPageChange={setPage} onPageSizeChange={setPageSize}
              ordering={ordering} onOrderingChange={setOrdering}
              onRowClick={(gr) => router.push(`/accounting/goods-receipts/${gr.id}`)}
              isLoading={isLoading}
              emptyState={
                <EmptyState
                  icon={<Package className="h-12 w-12" />}
                  title="No goods receipts yet"
                  description="Goods receipts are created from approved purchase orders when goods arrive."
                />
              }
            />
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={postDialog.open} onOpenChange={(open: boolean) => setPostDialog({ open, gr: null })}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Post Goods Receipt</AlertDialogTitle>
            <AlertDialogDescription>Post &quot;{postDialog.gr?.receipt_number}&quot;? This will update inventory quantities and mark the PO as received.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePost}>Post Receipt</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={voidDialog.open} onOpenChange={(open: boolean) => setVoidDialog({ open, gr: null })}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Void Goods Receipt</AlertDialogTitle>
            <AlertDialogDescription>Void &quot;{voidDialog.gr?.receipt_number}&quot;? This will reverse inventory quantities and update the PO status.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleVoid} className="bg-destructive text-destructive-foreground">Void Receipt</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
