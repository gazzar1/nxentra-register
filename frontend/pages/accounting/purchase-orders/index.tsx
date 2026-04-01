import React from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Eye, CheckCircle, XCircle, Lock, FileText, MoreHorizontal } from "lucide-react";
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
  usePaginatedPurchaseOrders,
  useApprovePurchaseOrder,
  useCancelPurchaseOrder,
  useClosePurchaseOrder,
} from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import type { PurchaseOrderListItem } from "@/types/purchases";
import { PO_STATUS_COLORS, PO_STATUS_LABELS } from "@/types/purchases";
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

export default function PurchaseOrdersPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { formatDate } = useCompanyFormat();
  const approvePO = useApprovePurchaseOrder();
  const cancelPO = useCancelPurchaseOrder();
  const closePO = useClosePurchaseOrder();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("-order_date");
  const [approveDialog, setApproveDialog] = useState<{ open: boolean; po: PurchaseOrderListItem | null }>({ open: false, po: null });
  const [cancelDialog, setCancelDialog] = useState<{ open: boolean; po: PurchaseOrderListItem | null }>({ open: false, po: null });
  const [closeDialog, setCloseDialog] = useState<{ open: boolean; po: PurchaseOrderListItem | null }>({ open: false, po: null });

  const { data: response, isLoading } = usePaginatedPurchaseOrders({
    page, page_size: pageSize, ordering,
  });

  const orders = response?.results || [];
  const totalCount = response?.count || 0;
  const totalPages = response?.total_pages || 1;

  const handleAction = async (action: "approve" | "cancel" | "close", po: PurchaseOrderListItem) => {
    try {
      if (action === "approve") await approvePO.mutateAsync(po.id);
      else if (action === "cancel") await cancelPO.mutateAsync({ id: po.id });
      else if (action === "close") await closePO.mutateAsync(po.id);
      toast({ title: `PO ${action}d`, description: `${po.order_number} has been ${action}d.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || `Failed to ${action} PO.`, variant: "destructive" });
    } finally {
      setApproveDialog({ open: false, po: null });
      setCancelDialog({ open: false, po: null });
      setCloseDialog({ open: false, po: null });
    }
  };

  const columns: ColumnDef<PurchaseOrderListItem>[] = [
    {
      key: "order_number",
      label: "PO #",
      sortable: true,
      render: (po) => (
        <Link href={`/accounting/purchase-orders/${po.id}`} className="font-mono text-sm font-medium hover:text-primary hover:underline ltr-code">
          {po.order_number}
        </Link>
      ),
    },
    {
      key: "order_date",
      label: "Date",
      sortable: true,
      render: (po) => <span className="text-sm text-muted-foreground">{formatDate(po.order_date)}</span>,
    },
    {
      key: "expected_delivery_date",
      label: "Expected Delivery",
      sortable: true,
      render: (po) => <span className="text-sm text-muted-foreground">{po.expected_delivery_date ? formatDate(po.expected_delivery_date) : "—"}</span>,
    },
    {
      key: "vendor_name",
      label: "Vendor",
      render: (po) => (
        <div>
          <span className="font-medium">{po.vendor_name}</span>
          <p className="text-sm text-muted-foreground font-mono ltr-code">{po.vendor_code}</p>
        </div>
      ),
    },
    {
      key: "total_amount",
      label: "Amount",
      sortable: true,
      className: "text-end",
      render: (po) => (
        <span className="font-mono ltr-number font-medium">
          {po.currency && <span className="text-muted-foreground text-xs me-1">{po.currency}</span>}
          {parseFloat(po.total_amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      ),
    },
    {
      key: "status",
      label: "Status",
      sortable: true,
      render: (po) => <Badge className={cn("text-xs", PO_STATUS_COLORS[po.status])}>{PO_STATUS_LABELS[po.status]}</Badge>,
    },
    {
      key: "actions",
      label: "",
      render: (po) => (
        <div className="flex items-center justify-end" onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm"><MoreHorizontal className="h-4 w-4" /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => router.push(`/accounting/purchase-orders/${po.id}`)}>
                <Eye className="h-4 w-4 me-2" />View
              </DropdownMenuItem>
              {po.status === "DRAFT" && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setApproveDialog({ open: true, po })}>
                    <CheckCircle className="h-4 w-4 me-2" />Approve
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setCancelDialog({ open: true, po })} className="text-destructive">
                    <XCircle className="h-4 w-4 me-2" />Cancel
                  </DropdownMenuItem>
                </>
              )}
              {po.status === "APPROVED" && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => router.push(`/accounting/goods-receipts/new?po=${po.id}`)}>
                    <FileText className="h-4 w-4 me-2" />Receive Goods
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setCancelDialog({ open: true, po })} className="text-destructive">
                    <XCircle className="h-4 w-4 me-2" />Cancel
                  </DropdownMenuItem>
                </>
              )}
              {(po.status === "PARTIALLY_RECEIVED" || po.status === "FULLY_RECEIVED") && (
                <>
                  <DropdownMenuSeparator />
                  {po.status === "PARTIALLY_RECEIVED" && (
                    <DropdownMenuItem onClick={() => router.push(`/accounting/goods-receipts/new?po=${po.id}`)}>
                      <FileText className="h-4 w-4 me-2" />Receive More
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onClick={() => setCloseDialog({ open: true, po })}>
                    <Lock className="h-4 w-4 me-2" />Close PO
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
          title="Purchase Orders"
          subtitle="Manage purchase orders, goods receipts, and vendor billing"
          actions={
            <Link href="/accounting/purchase-orders/new">
              <Button><Plus className="h-4 w-4 me-2" />New Purchase Order</Button>
            </Link>
          }
        />
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input placeholder="Search purchase orders..." value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} className="ps-10" />
              </div>
            </div>
            <PaginatedTable
              data={orders}
              columns={columns}
              keyExtractor={(po) => po.id}
              page={page} pageSize={pageSize} totalCount={totalCount} totalPages={totalPages}
              onPageChange={setPage} onPageSizeChange={setPageSize}
              ordering={ordering} onOrderingChange={setOrdering}
              onRowClick={(po) => router.push(`/accounting/purchase-orders/${po.id}`)}
              isLoading={isLoading}
              emptyState={<EmptyState title="No purchase orders yet" description="Create your first purchase order to start the procurement workflow." />}
            />
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={approveDialog.open} onOpenChange={(open: boolean) => setApproveDialog({ open, po: null })}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Approve Purchase Order</AlertDialogTitle>
            <AlertDialogDescription>Approve &quot;{approveDialog.po?.order_number}&quot;? This will allow goods to be received against it.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => approveDialog.po && handleAction("approve", approveDialog.po)}>Approve</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={cancelDialog.open} onOpenChange={(open: boolean) => setCancelDialog({ open, po: null })}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Cancel Purchase Order</AlertDialogTitle>
            <AlertDialogDescription>Cancel &quot;{cancelDialog.po?.order_number}&quot;? This action cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep</AlertDialogCancel>
            <AlertDialogAction onClick={() => cancelDialog.po && handleAction("cancel", cancelDialog.po)} className="bg-destructive text-destructive-foreground">Cancel PO</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={closeDialog.open} onOpenChange={(open: boolean) => setCloseDialog({ open, po: null })}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Close Purchase Order</AlertDialogTitle>
            <AlertDialogDescription>Close &quot;{closeDialog.po?.order_number}&quot;? Any remaining undelivered quantities will be abandoned.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep Open</AlertDialogCancel>
            <AlertDialogAction onClick={() => closeDialog.po && handleAction("close", closeDialog.po)}>Close PO</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
