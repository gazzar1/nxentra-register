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
  usePaginatedSalesInvoices,
  useDeleteSalesInvoice,
  usePostSalesInvoice,
  useVoidSalesInvoice,
} from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { SalesInvoiceListItem } from "@/types/sales";
import { INVOICE_STATUS_COLORS, INVOICE_STATUS_LABELS } from "@/types/sales";
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

export default function SalesInvoicesPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { formatDate } = useCompanyFormat();
  const deleteInvoice = useDeleteSalesInvoice();
  const postInvoice = usePostSalesInvoice();
  const voidInvoice = useVoidSalesInvoice();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("-invoice_date");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; invoice: SalesInvoiceListItem | null }>({ open: false, invoice: null });
  const [postDialog, setPostDialog] = useState<{ open: boolean; invoice: SalesInvoiceListItem | null }>({ open: false, invoice: null });
  const [voidDialog, setVoidDialog] = useState<{ open: boolean; invoice: SalesInvoiceListItem | null }>({ open: false, invoice: null });

  const { data: response, isLoading } = usePaginatedSalesInvoices({
    search: search || undefined,
    page,
    page_size: pageSize,
    ordering,
  });

  const invoices = response?.results || [];
  const totalCount = response?.count || 0;
  const totalPages = response?.total_pages || 1;

  const handleSearchChange = (value: string) => { setSearch(value); setPage(1); };

  const handleDelete = async () => {
    if (!deleteDialog.invoice) return;
    try {
      await deleteInvoice.mutateAsync(deleteDialog.invoice.id);
      toast({ title: "Invoice deleted", description: `Invoice ${deleteDialog.invoice.invoice_number} has been deleted.` });
    } catch (error) {
      toast({ title: "Error", description: "Failed to delete invoice.", variant: "destructive" });
    } finally {
      setDeleteDialog({ open: false, invoice: null });
    }
  };

  const handlePost = async () => {
    if (!postDialog.invoice) return;
    try {
      await postInvoice.mutateAsync(postDialog.invoice.id);
      toast({ title: "Invoice posted", description: `Invoice ${postDialog.invoice.invoice_number} has been posted to the general ledger.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.error || "Failed to post invoice.", variant: "destructive" });
    } finally {
      setPostDialog({ open: false, invoice: null });
    }
  };

  const handleVoid = async () => {
    if (!voidDialog.invoice) return;
    try {
      await voidInvoice.mutateAsync({ id: voidDialog.invoice.id });
      toast({ title: "Invoice voided", description: `Invoice ${voidDialog.invoice.invoice_number} has been voided with a reversing entry.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.error || "Failed to void invoice.", variant: "destructive" });
    } finally {
      setVoidDialog({ open: false, invoice: null });
    }
  };

  const columns: ColumnDef<SalesInvoiceListItem>[] = [
    {
      key: "invoice_number",
      label: "Invoice #",
      sortable: true,
      render: (inv) => (
        <Link href={`/accounting/sales-invoices/${inv.id}`} className="font-mono text-sm font-medium hover:text-primary hover:underline ltr-code">
          {inv.invoice_number}
        </Link>
      ),
    },
    {
      key: "invoice_date",
      label: "Date",
      sortable: true,
      render: (inv) => <span className="text-sm text-muted-foreground">{formatDate(inv.invoice_date)}</span>,
    },
    {
      key: "customer_name",
      label: "Customer",
      render: (inv) => (
        <div>
          <span className="font-medium">{inv.customer_name}</span>
          <p className="text-sm text-muted-foreground font-mono ltr-code">{inv.customer_code}</p>
        </div>
      ),
    },
    {
      key: "total_amount",
      label: "Amount",
      sortable: true,
      className: "text-end",
      render: (inv) => (
        <span className="font-mono ltr-number font-medium">
          {inv.currency && <span className="text-muted-foreground text-xs me-1">{inv.currency}</span>}
          {parseFloat(inv.total_amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      ),
    },
    {
      key: "status",
      label: "Status",
      sortable: true,
      render: (inv) => (
        <Badge className={cn("text-xs", INVOICE_STATUS_COLORS[inv.status])}>
          {INVOICE_STATUS_LABELS[inv.status]}
        </Badge>
      ),
    },
    {
      key: "actions",
      label: "",
      render: (inv) => (
        <div className="flex items-center justify-end" onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm"><MoreHorizontal className="h-4 w-4" /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => router.push(`/accounting/sales-invoices/${inv.id}`)}>
                <Eye className="h-4 w-4 me-2" />View
              </DropdownMenuItem>
              {inv.status === "DRAFT" && (
                <>
                  <DropdownMenuItem onClick={() => router.push(`/accounting/sales-invoices/${inv.id}/edit`)}>
                    <Pencil className="h-4 w-4 me-2" />Edit
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setPostDialog({ open: true, invoice: inv })}>
                    <Send className="h-4 w-4 me-2" />Post Invoice
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setDeleteDialog({ open: true, invoice: inv })} className="text-destructive">
                    <Trash2 className="h-4 w-4 me-2" />Delete
                  </DropdownMenuItem>
                </>
              )}
              {inv.status === "POSTED" && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setVoidDialog({ open: true, invoice: inv })} className="text-destructive">
                    <XCircle className="h-4 w-4 me-2" />Void Invoice
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
          title="Sales Invoices"
          subtitle="Manage your sales invoices and track accounts receivable"
          actions={
            <Link href="/accounting/sales-invoices/new">
              <Button><Plus className="h-4 w-4 me-2" />New Invoice</Button>
            </Link>
          }
        />
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input placeholder="Search invoices..." value={search} onChange={(e) => handleSearchChange(e.target.value)} className="ps-10" />
              </div>
            </div>
            <PaginatedTable
              data={invoices}
              columns={columns}
              keyExtractor={(inv) => inv.id}
              page={page}
              pageSize={pageSize}
              totalCount={totalCount}
              totalPages={totalPages}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              ordering={ordering}
              onOrderingChange={setOrdering}
              onRowClick={(inv) => router.push(`/accounting/sales-invoices/${inv.id}`)}
              isLoading={isLoading}
              emptyState={
                <EmptyState
                  icon={<Receipt className="h-12 w-12" />}
                  title="No invoices yet"
                  description="Create your first sales invoice to start tracking revenue."
                  action={<Link href="/accounting/sales-invoices/new"><Button><Plus className="h-4 w-4 me-2" />New Invoice</Button></Link>}
                />
              }
            />
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, invoice: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Invoice</AlertDialogTitle>
            <AlertDialogDescription>Are you sure you want to delete invoice &quot;{deleteDialog.invoice?.invoice_number}&quot;? This action cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={postDialog.open} onOpenChange={(open: boolean) => setPostDialog({ open, invoice: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Post Invoice</AlertDialogTitle>
            <AlertDialogDescription>Are you sure you want to post invoice &quot;{postDialog.invoice?.invoice_number}&quot;? This will create a journal entry and the invoice cannot be edited afterwards.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePost}>Post Invoice</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={voidDialog.open} onOpenChange={(open: boolean) => setVoidDialog({ open, invoice: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Void Invoice</AlertDialogTitle>
            <AlertDialogDescription>Are you sure you want to void invoice &quot;{voidDialog.invoice?.invoice_number}&quot;? This will create a reversing journal entry to cancel the original posting.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleVoid} className="bg-destructive text-destructive-foreground">Void Invoice</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
