import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Receipt, Pencil, Trash2, Send, XCircle, Eye, MoreHorizontal } from "lucide-react";
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
  usePaginatedPurchaseBills,
  useDeletePurchaseBill,
  usePostPurchaseBill,
  useVoidPurchaseBill,
} from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import type { PurchaseBillListItem } from "@/types/purchases";
import { BILL_STATUS_COLORS, BILL_STATUS_LABELS } from "@/types/purchases";
import { cn } from "@/lib/cn";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export default function PurchaseBillsPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { formatDate } = useCompanyFormat();
  const deleteBill = useDeletePurchaseBill();
  const postBill = usePostPurchaseBill();
  const voidBill = useVoidPurchaseBill();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("-bill_date");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; bill: PurchaseBillListItem | null }>({ open: false, bill: null });
  const [postDialog, setPostDialog] = useState<{ open: boolean; bill: PurchaseBillListItem | null }>({ open: false, bill: null });
  const [voidDialog, setVoidDialog] = useState<{ open: boolean; bill: PurchaseBillListItem | null }>({ open: false, bill: null });

  const { data: response, isLoading } = usePaginatedPurchaseBills({
    search: search || undefined,
    page,
    page_size: pageSize,
    ordering,
  });

  const bills = response?.results || [];
  const totalCount = response?.count || 0;
  const totalPages = response?.total_pages || 1;

  const handleSearchChange = (value: string) => { setSearch(value); setPage(1); };

  const handleDelete = async () => {
    if (!deleteDialog.bill) return;
    try {
      await deleteBill.mutateAsync(deleteDialog.bill.id);
      toast({ title: "Bill deleted", description: `Bill ${deleteDialog.bill.bill_number} has been deleted.` });
    } catch (error) {
      toast({ title: "Error", description: "Failed to delete bill.", variant: "destructive" });
    } finally {
      setDeleteDialog({ open: false, bill: null });
    }
  };

  const handlePost = async () => {
    if (!postDialog.bill) return;
    try {
      await postBill.mutateAsync(postDialog.bill.id);
      toast({ title: "Bill posted", description: `Bill ${postDialog.bill.bill_number} has been posted to the general ledger.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to post bill.", variant: "destructive" });
    } finally {
      setPostDialog({ open: false, bill: null });
    }
  };

  const handleVoid = async () => {
    if (!voidDialog.bill) return;
    try {
      await voidBill.mutateAsync({ id: voidDialog.bill.id });
      toast({ title: "Bill voided", description: `Bill ${voidDialog.bill.bill_number} has been voided with a reversing entry.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to void bill.", variant: "destructive" });
    } finally {
      setVoidDialog({ open: false, bill: null });
    }
  };

  const columns: ColumnDef<PurchaseBillListItem>[] = [
    {
      key: "bill_number",
      label: "Bill #",
      sortable: true,
      render: (bill) => (
        <Link href={`/accounting/purchase-bills/${bill.id}`} className="font-mono text-sm font-medium hover:text-primary hover:underline ltr-code">
          {bill.bill_number}
        </Link>
      ),
    },
    {
      key: "bill_date",
      label: "Date",
      sortable: true,
      render: (bill) => <span className="text-sm text-muted-foreground">{formatDate(bill.bill_date)}</span>,
    },
    {
      key: "vendor_name",
      label: "Vendor",
      render: (bill) => (
        <div>
          <span className="font-medium truncate block">{bill.vendor_name}</span>
          <p className="text-sm text-muted-foreground font-mono ltr-code">{bill.vendor_code}</p>
        </div>
      ),
    },
    {
      key: "vendor_bill_reference",
      label: "Vendor Ref",
      render: (bill) => <span className="text-sm text-muted-foreground">{bill.vendor_bill_reference || "—"}</span>,
    },
    {
      key: "total_amount",
      label: "Amount",
      sortable: true,
      className: "text-end",
      render: (bill) => (
        <span className="font-mono ltr-number font-medium">
          {bill.currency && <span className="text-muted-foreground text-xs me-1">{bill.currency}</span>}
          {parseFloat(bill.total_amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      ),
    },
    {
      key: "status",
      label: "Status",
      sortable: true,
      render: (bill) => (
        <Badge className={cn("text-xs", BILL_STATUS_COLORS[bill.status])}>
          {BILL_STATUS_LABELS[bill.status]}
        </Badge>
      ),
    },
    {
      key: "actions",
      label: "",
      render: (bill) => (
        <div className="flex items-center justify-end" onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm"><MoreHorizontal className="h-4 w-4" /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => router.push(`/accounting/purchase-bills/${bill.id}`)}>
                <Eye className="h-4 w-4 me-2" />View
              </DropdownMenuItem>
              {bill.status === "DRAFT" && (
                <>
                  <DropdownMenuItem onClick={() => router.push(`/accounting/purchase-bills/${bill.id}/edit`)}>
                    <Pencil className="h-4 w-4 me-2" />Edit
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setPostDialog({ open: true, bill })}>
                    <Send className="h-4 w-4 me-2" />Post Bill
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setDeleteDialog({ open: true, bill })} className="text-destructive">
                    <Trash2 className="h-4 w-4 me-2" />Delete
                  </DropdownMenuItem>
                </>
              )}
              {bill.status === "POSTED" && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setVoidDialog({ open: true, bill })} className="text-destructive">
                    <XCircle className="h-4 w-4 me-2" />Void Bill
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
          title="Purchase Bills"
          subtitle="Manage your vendor bills and track accounts payable"
          actions={
            <Link href="/accounting/purchase-bills/new">
              <Button><Plus className="h-4 w-4 me-2" />New Bill</Button>
            </Link>
          }
        />
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input placeholder="Search bills..." value={search} onChange={(e) => handleSearchChange(e.target.value)} className="ps-10" />
              </div>
            </div>
            <PaginatedTable
              data={bills}
              columns={columns}
              keyExtractor={(bill) => bill.id}
              page={page}
              pageSize={pageSize}
              totalCount={totalCount}
              totalPages={totalPages}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              ordering={ordering}
              onOrderingChange={setOrdering}
              onRowClick={(bill) => router.push(`/accounting/purchase-bills/${bill.id}`)}
              isLoading={isLoading}
              emptyState={
                <EmptyState
                  icon={<Receipt className="h-12 w-12" />}
                  title="No bills yet"
                  description="Create your first purchase bill to start tracking expenses."
                  action={<Link href="/accounting/purchase-bills/new"><Button><Plus className="h-4 w-4 me-2" />New Bill</Button></Link>}
                />
              }
            />
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, bill: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Bill</AlertDialogTitle>
            <AlertDialogDescription>Are you sure you want to delete bill &quot;{deleteDialog.bill?.bill_number}&quot;? This action cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={postDialog.open} onOpenChange={(open: boolean) => setPostDialog({ open, bill: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Post Bill</AlertDialogTitle>
            <AlertDialogDescription>Are you sure you want to post bill &quot;{postDialog.bill?.bill_number}&quot;? This will create a journal entry and the bill cannot be edited afterwards.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePost}>Post Bill</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={voidDialog.open} onOpenChange={(open: boolean) => setVoidDialog({ open, bill: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Void Bill</AlertDialogTitle>
            <AlertDialogDescription>Are you sure you want to void bill &quot;{voidDialog.bill?.bill_number}&quot;? This will create a reversing journal entry to cancel the original posting.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleVoid} className="bg-destructive text-destructive-foreground">Void Bill</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
