import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Receipt, Pencil, Trash2, Send, XCircle, Eye } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import {
  useSalesInvoices,
  useDeleteSalesInvoice,
  usePostSalesInvoice,
  useVoidSalesInvoice,
} from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { SalesInvoiceListItem, SalesInvoiceStatus } from "@/types/sales";
import { INVOICE_STATUS_COLORS, INVOICE_STATUS_LABELS } from "@/types/sales";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MoreHorizontal } from "lucide-react";

export default function SalesInvoicesPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: invoices, isLoading } = useSalesInvoices();
  const deleteInvoice = useDeleteSalesInvoice();
  const postInvoice = usePostSalesInvoice();
  const voidInvoice = useVoidSalesInvoice();
  const [search, setSearch] = useState("");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; invoice: SalesInvoiceListItem | null }>({
    open: false,
    invoice: null,
  });
  const [postDialog, setPostDialog] = useState<{ open: boolean; invoice: SalesInvoiceListItem | null }>({
    open: false,
    invoice: null,
  });
  const [voidDialog, setVoidDialog] = useState<{ open: boolean; invoice: SalesInvoiceListItem | null }>({
    open: false,
    invoice: null,
  });

  const filteredInvoices = invoices?.filter((inv) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      inv.invoice_number.toLowerCase().includes(searchLower) ||
      inv.customer_name?.toLowerCase().includes(searchLower) ||
      inv.customer_code?.toLowerCase().includes(searchLower)
    );
  });

  const handleDelete = async () => {
    if (!deleteDialog.invoice) return;

    try {
      await deleteInvoice.mutateAsync(deleteDialog.invoice.id);
      toast({
        title: "Invoice deleted",
        description: `Invoice ${deleteDialog.invoice.invoice_number} has been deleted.`,
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete invoice.",
        variant: "destructive",
      });
    } finally {
      setDeleteDialog({ open: false, invoice: null });
    }
  };

  const handlePost = async () => {
    if (!postDialog.invoice) return;

    try {
      await postInvoice.mutateAsync(postDialog.invoice.id);
      toast({
        title: "Invoice posted",
        description: `Invoice ${postDialog.invoice.invoice_number} has been posted to the general ledger.`,
      });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.error || "Failed to post invoice.",
        variant: "destructive",
      });
    } finally {
      setPostDialog({ open: false, invoice: null });
    }
  };

  const handleVoid = async () => {
    if (!voidDialog.invoice) return;

    try {
      await voidInvoice.mutateAsync({ id: voidDialog.invoice.id });
      toast({
        title: "Invoice voided",
        description: `Invoice ${voidDialog.invoice.invoice_number} has been voided with a reversing entry.`,
      });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.error || "Failed to void invoice.",
        variant: "destructive",
      });
    } finally {
      setVoidDialog({ open: false, invoice: null });
    }
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Sales Invoices"
          subtitle="Manage your sales invoices and track accounts receivable"
          actions={
            <Link href="/accounting/sales-invoices/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />
                New Invoice
              </Button>
            </Link>
          }
        />

        <Card>
          <CardContent className="p-6">
            {/* Search */}
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search invoices..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="ps-10"
                />
              </div>
            </div>

            {/* Content */}
            {isLoading ? (
              <LoadingSpinner />
            ) : !filteredInvoices?.length ? (
              <EmptyState
                icon={<Receipt className="h-12 w-12" />}
                title="No invoices yet"
                description="Create your first sales invoice to start tracking revenue."
                action={
                  <Link href="/accounting/sales-invoices/new">
                    <Button>
                      <Plus className="h-4 w-4 me-2" />
                      New Invoice
                    </Button>
                  </Link>
                }
              />
            ) : (
              <div className="space-y-2">
                {/* Header */}
                <div className="grid grid-cols-12 gap-4 px-4 py-2 text-sm font-medium text-muted-foreground border-b">
                  <div className="col-span-2">Invoice #</div>
                  <div className="col-span-2">Date</div>
                  <div className="col-span-3">Customer</div>
                  <div className="col-span-2 text-end">Amount</div>
                  <div className="col-span-2">Status</div>
                  <div className="col-span-1"></div>
                </div>

                {/* Rows */}
                {filteredInvoices.map((invoice) => (
                  <div
                    key={invoice.id}
                    className="grid grid-cols-12 gap-4 px-4 py-3 rounded-lg border hover:bg-muted/50 transition-colors items-center"
                  >
                    <div className="col-span-2">
                      <Link
                        href={`/accounting/sales-invoices/${invoice.id}`}
                        className="font-mono text-sm font-medium hover:text-primary hover:underline ltr-code"
                      >
                        {invoice.invoice_number}
                      </Link>
                    </div>
                    <div className="col-span-2 text-sm text-muted-foreground">
                      {formatDate(invoice.invoice_date)}
                    </div>
                    <div className="col-span-3">
                      <span className="font-medium">{invoice.customer_name}</span>
                      <p className="text-sm text-muted-foreground font-mono ltr-code">
                        {invoice.customer_code}
                      </p>
                    </div>
                    <div className="col-span-2 text-end font-mono ltr-number font-medium">
                      {invoice.currency && <span className="text-muted-foreground text-xs me-1">{invoice.currency}</span>}
                      {parseFloat(invoice.total_amount).toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </div>
                    <div className="col-span-2">
                      <Badge className={cn("text-xs", INVOICE_STATUS_COLORS[invoice.status])}>
                        {INVOICE_STATUS_LABELS[invoice.status]}
                      </Badge>
                    </div>
                    <div className="col-span-1 flex items-center justify-end">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="sm">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => router.push(`/accounting/sales-invoices/${invoice.id}`)}>
                            <Eye className="h-4 w-4 me-2" />
                            View
                          </DropdownMenuItem>
                          {invoice.status === "DRAFT" && (
                            <>
                              <DropdownMenuItem onClick={() => router.push(`/accounting/sales-invoices/${invoice.id}/edit`)}>
                                <Pencil className="h-4 w-4 me-2" />
                                Edit
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem onClick={() => setPostDialog({ open: true, invoice })}>
                                <Send className="h-4 w-4 me-2" />
                                Post Invoice
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                onClick={() => setDeleteDialog({ open: true, invoice })}
                                className="text-destructive"
                              >
                                <Trash2 className="h-4 w-4 me-2" />
                                Delete
                              </DropdownMenuItem>
                            </>
                          )}
                          {invoice.status === "POSTED" && (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                onClick={() => setVoidDialog({ open: true, invoice })}
                                className="text-destructive"
                              >
                                <XCircle className="h-4 w-4 me-2" />
                                Void Invoice
                              </DropdownMenuItem>
                            </>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Delete confirmation dialog */}
      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, invoice: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Invoice</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete invoice &quot;{deleteDialog.invoice?.invoice_number}&quot;? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground">
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Post confirmation dialog */}
      <AlertDialog open={postDialog.open} onOpenChange={(open: boolean) => setPostDialog({ open, invoice: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Post Invoice</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to post invoice &quot;{postDialog.invoice?.invoice_number}&quot;? This will create a journal entry
              and the invoice cannot be edited afterwards.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePost}>
              Post Invoice
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Void confirmation dialog */}
      <AlertDialog open={voidDialog.open} onOpenChange={(open: boolean) => setVoidDialog({ open, invoice: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Void Invoice</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to void invoice &quot;{voidDialog.invoice?.invoice_number}&quot;? This will create a reversing
              journal entry to cancel the original posting.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleVoid} className="bg-destructive text-destructive-foreground">
              Void Invoice
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
