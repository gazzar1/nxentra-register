import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Receipt, Eye, Send, XCircle, MoreHorizontal } from "lucide-react";
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
  usePaginatedPurchaseCreditNotes,
  usePostPurchaseCreditNote,
  useVoidPurchaseCreditNote,
} from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import type { PurchaseCreditNoteListItem } from "@/types/purchases";
import { PCN_STATUS_COLORS, PCN_STATUS_LABELS, PCN_REASON_LABELS } from "@/types/purchases";
import { cn as clsx } from "@/lib/cn";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export default function PurchaseCreditNotesPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { formatDate } = useCompanyFormat();
  const postCN = usePostPurchaseCreditNote();
  const voidCN = useVoidPurchaseCreditNote();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("-credit_note_date");
  const [postDialog, setPostDialog] = useState<{ open: boolean; cn: PurchaseCreditNoteListItem | null }>({ open: false, cn: null });
  const [voidDialog, setVoidDialog] = useState<{ open: boolean; cn: PurchaseCreditNoteListItem | null }>({ open: false, cn: null });

  const { data: response, isLoading } = usePaginatedPurchaseCreditNotes({
    search: search || undefined,
    page,
    page_size: pageSize,
    ordering,
  });

  const creditNotes = response?.results || [];
  const totalCount = response?.count || 0;
  const totalPages = response?.total_pages || 1;

  const handleSearchChange = (value: string) => { setSearch(value); setPage(1); };

  const handlePost = async () => {
    if (!postDialog.cn) return;
    try {
      await postCN.mutateAsync(postDialog.cn.id);
      toast({ title: "Credit note posted", description: `${postDialog.cn.credit_note_number} has been posted. AP balance reduced.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to post credit note.", variant: "destructive" });
    } finally {
      setPostDialog({ open: false, cn: null });
    }
  };

  const handleVoid = async () => {
    if (!voidDialog.cn) return;
    try {
      await voidCN.mutateAsync({ id: voidDialog.cn.id });
      toast({ title: "Credit note voided", description: `${voidDialog.cn.credit_note_number} has been voided.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to void credit note.", variant: "destructive" });
    } finally {
      setVoidDialog({ open: false, cn: null });
    }
  };

  const columns: ColumnDef<PurchaseCreditNoteListItem>[] = [
    {
      key: "credit_note_number",
      label: "Credit Note #",
      sortable: true,
      render: (cn) => (
        <Link href={`/accounting/purchase-credit-notes/${cn.id}`} className="font-mono text-sm font-medium hover:text-primary hover:underline ltr-code">
          {cn.credit_note_number}
        </Link>
      ),
    },
    {
      key: "credit_note_date",
      label: "Date",
      sortable: true,
      render: (cn) => <span className="text-sm text-muted-foreground">{formatDate(cn.credit_note_date)}</span>,
    },
    {
      key: "bill_number",
      label: "Original Bill",
      render: (cn) => (
        <Link href={`/accounting/purchase-bills/${cn.bill}`} className="font-mono text-sm hover:text-primary hover:underline ltr-code">
          {cn.bill_number}
        </Link>
      ),
    },
    {
      key: "vendor_name",
      label: "Vendor",
      render: (cn) => (
        <div>
          <span className="font-medium">{cn.vendor_name}</span>
          <p className="text-sm text-muted-foreground font-mono ltr-code">{cn.vendor_code}</p>
        </div>
      ),
    },
    {
      key: "reason",
      label: "Reason",
      render: (cn) => (
        <span className="text-sm">{PCN_REASON_LABELS[cn.reason] || cn.reason}</span>
      ),
    },
    {
      key: "total_amount",
      label: "Amount",
      sortable: true,
      className: "text-end",
      render: (cn) => (
        <span className="font-mono ltr-number font-medium text-red-600">
          -{parseFloat(cn.total_amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      ),
    },
    {
      key: "status",
      label: "Status",
      sortable: true,
      render: (cn) => (
        <Badge className={clsx("text-xs", PCN_STATUS_COLORS[cn.status])}>
          {PCN_STATUS_LABELS[cn.status]}
        </Badge>
      ),
    },
    {
      key: "actions",
      label: "",
      render: (cnItem) => (
        <div className="flex items-center justify-end" onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm"><MoreHorizontal className="h-4 w-4" /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => router.push(`/accounting/purchase-credit-notes/${cnItem.id}`)}>
                <Eye className="h-4 w-4 me-2" />View
              </DropdownMenuItem>
              {cnItem.status === "DRAFT" && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setPostDialog({ open: true, cn: cnItem })}>
                    <Send className="h-4 w-4 me-2" />Post Credit Note
                  </DropdownMenuItem>
                </>
              )}
              {cnItem.status === "POSTED" && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => setVoidDialog({ open: true, cn: cnItem })} className="text-destructive">
                    <XCircle className="h-4 w-4 me-2" />Void Credit Note
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
          title="Purchase Credit Notes"
          subtitle="Manage vendor returns and debit notes"
          actions={
            <Link href="/accounting/purchase-credit-notes/new">
              <Button><Plus className="h-4 w-4 me-2" />New Credit Note</Button>
            </Link>
          }
        />
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input placeholder="Search credit notes..." value={search} onChange={(e) => handleSearchChange(e.target.value)} className="ps-10" />
              </div>
            </div>
            <PaginatedTable
              data={creditNotes}
              columns={columns}
              keyExtractor={(cn) => cn.id}
              page={page}
              pageSize={pageSize}
              totalCount={totalCount}
              totalPages={totalPages}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              ordering={ordering}
              onOrderingChange={setOrdering}
              onRowClick={(cn) => router.push(`/accounting/purchase-credit-notes/${cn.id}`)}
              isLoading={isLoading}
              emptyState={
                <EmptyState
                  icon={<Receipt className="h-12 w-12" />}
                  title="No purchase credit notes yet"
                  description="Credit notes are created from posted purchase bills to process vendor returns or price adjustments."
                />
              }
            />
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={postDialog.open} onOpenChange={(open: boolean) => setPostDialog({ open, cn: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Post Credit Note</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to post credit note &quot;{postDialog.cn?.credit_note_number}&quot;?
              This will create a journal entry that reduces the vendor&apos;s payable balance.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePost}>Post Credit Note</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={voidDialog.open} onOpenChange={(open: boolean) => setVoidDialog({ open, cn: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Void Credit Note</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to void credit note &quot;{voidDialog.cn?.credit_note_number}&quot;?
              This will reverse the credit and restore the vendor&apos;s payable balance.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleVoid} className="bg-destructive text-destructive-foreground">Void Credit Note</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
