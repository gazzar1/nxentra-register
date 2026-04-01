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
  usePaginatedCreditNotes,
  usePostCreditNote,
  useVoidCreditNote,
} from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { CreditNoteListItem } from "@/types/sales";
import {
  CREDIT_NOTE_STATUS_COLORS,
  CREDIT_NOTE_STATUS_LABELS,
  CREDIT_NOTE_REASON_LABELS,
} from "@/types/sales";
import { cn as clsx } from "@/lib/cn";
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

export default function CreditNotesPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { formatDate } = useCompanyFormat();
  const postCreditNote = usePostCreditNote();
  const voidCreditNote = useVoidCreditNote();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("-credit_note_date");
  const [postDialog, setPostDialog] = useState<{ open: boolean; cn: CreditNoteListItem | null }>({ open: false, cn: null });
  const [voidDialog, setVoidDialog] = useState<{ open: boolean; cn: CreditNoteListItem | null }>({ open: false, cn: null });

  const { data: response, isLoading } = usePaginatedCreditNotes({
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
      await postCreditNote.mutateAsync(postDialog.cn.id);
      toast({ title: "Credit note posted", description: `Credit note ${postDialog.cn.credit_note_number} has been posted.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to post credit note.", variant: "destructive" });
    } finally {
      setPostDialog({ open: false, cn: null });
    }
  };

  const handleVoid = async () => {
    if (!voidDialog.cn) return;
    try {
      await voidCreditNote.mutateAsync({ id: voidDialog.cn.id });
      toast({ title: "Credit note voided", description: `Credit note ${voidDialog.cn.credit_note_number} has been voided.` });
    } catch (error: any) {
      toast({ title: "Error", description: error?.response?.data?.detail || "Failed to void credit note.", variant: "destructive" });
    } finally {
      setVoidDialog({ open: false, cn: null });
    }
  };

  const columns: ColumnDef<CreditNoteListItem>[] = [
    {
      key: "credit_note_number",
      label: "Credit Note #",
      sortable: true,
      render: (cn) => (
        <Link href={`/accounting/credit-notes/${cn.id}`} className="font-mono text-sm font-medium hover:text-primary hover:underline ltr-code">
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
      key: "invoice_number",
      label: "Original Invoice",
      render: (cn) => (
        <Link href={`/accounting/sales-invoices/${cn.invoice}`} className="font-mono text-sm hover:text-primary hover:underline ltr-code">
          {cn.invoice_number}
        </Link>
      ),
    },
    {
      key: "customer_name",
      label: "Customer",
      render: (cn) => (
        <div>
          <span className="font-medium">{cn.customer_name}</span>
          <p className="text-sm text-muted-foreground font-mono ltr-code">{cn.customer_code}</p>
        </div>
      ),
    },
    {
      key: "reason",
      label: "Reason",
      render: (cn) => (
        <span className="text-sm">{CREDIT_NOTE_REASON_LABELS[cn.reason] || cn.reason}</span>
      ),
    },
    {
      key: "total_amount",
      label: "Amount",
      sortable: true,
      className: "text-end",
      render: (cn) => (
        <span className="font-mono ltr-number font-medium text-red-600">
          {cn.currency && <span className="text-muted-foreground text-xs me-1">{cn.currency}</span>}
          -{parseFloat(cn.total_amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      ),
    },
    {
      key: "status",
      label: "Status",
      sortable: true,
      render: (cn) => (
        <Badge className={clsx("text-xs", CREDIT_NOTE_STATUS_COLORS[cn.status])}>
          {CREDIT_NOTE_STATUS_LABELS[cn.status]}
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
              <DropdownMenuItem onClick={() => router.push(`/accounting/credit-notes/${cnItem.id}`)}>
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
          title="Credit Notes"
          subtitle="Manage sales credit notes and returns"
          actions={
            <Link href="/accounting/credit-notes/new">
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
              onRowClick={(cn) => router.push(`/accounting/credit-notes/${cn.id}`)}
              isLoading={isLoading}
              emptyState={
                <EmptyState
                  icon={<Receipt className="h-12 w-12" />}
                  title="No credit notes yet"
                  description="Credit notes are created from posted invoices to process returns or adjustments."
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
              This will create a reversing journal entry and reduce the customer&apos;s receivable balance.
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
              This will reverse the credit and restore the customer&apos;s receivable balance.
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
