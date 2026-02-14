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
  usePurchaseBills,
  useDeletePurchaseBill,
  usePostPurchaseBill,
  useVoidPurchaseBill,
} from "@/queries/usePurchases";
import { useToast } from "@/components/ui/toaster";
import type { PurchaseBillListItem, PurchaseBillStatus } from "@/types/purchases";
import { BILL_STATUS_COLORS, BILL_STATUS_LABELS } from "@/types/purchases";
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

export default function PurchaseBillsPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: bills, isLoading } = usePurchaseBills();
  const deleteBill = useDeletePurchaseBill();
  const postBill = usePostPurchaseBill();
  const voidBill = useVoidPurchaseBill();
  const [search, setSearch] = useState("");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; bill: PurchaseBillListItem | null }>({
    open: false,
    bill: null,
  });
  const [postDialog, setPostDialog] = useState<{ open: boolean; bill: PurchaseBillListItem | null }>({
    open: false,
    bill: null,
  });
  const [voidDialog, setVoidDialog] = useState<{ open: boolean; bill: PurchaseBillListItem | null }>({
    open: false,
    bill: null,
  });

  const filteredBills = bills?.filter((bill) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      bill.bill_number.toLowerCase().includes(searchLower) ||
      bill.vendor_name?.toLowerCase().includes(searchLower) ||
      bill.vendor_code?.toLowerCase().includes(searchLower) ||
      bill.vendor_bill_reference?.toLowerCase().includes(searchLower)
    );
  });

  const handleDelete = async () => {
    if (!deleteDialog.bill) return;

    try {
      await deleteBill.mutateAsync(deleteDialog.bill.id);
      toast({
        title: "Bill deleted",
        description: `Bill ${deleteDialog.bill.bill_number} has been deleted.`,
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete bill.",
        variant: "destructive",
      });
    } finally {
      setDeleteDialog({ open: false, bill: null });
    }
  };

  const handlePost = async () => {
    if (!postDialog.bill) return;

    try {
      await postBill.mutateAsync(postDialog.bill.id);
      toast({
        title: "Bill posted",
        description: `Bill ${postDialog.bill.bill_number} has been posted to the general ledger.`,
      });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to post bill.",
        variant: "destructive",
      });
    } finally {
      setPostDialog({ open: false, bill: null });
    }
  };

  const handleVoid = async () => {
    if (!voidDialog.bill) return;

    try {
      await voidBill.mutateAsync({ id: voidDialog.bill.id });
      toast({
        title: "Bill voided",
        description: `Bill ${voidDialog.bill.bill_number} has been voided with a reversing entry.`,
      });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to void bill.",
        variant: "destructive",
      });
    } finally {
      setVoidDialog({ open: false, bill: null });
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
          title="Purchase Bills"
          subtitle="Manage your vendor bills and track accounts payable"
          actions={
            <Link href="/accounting/purchase-bills/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />
                New Bill
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
                  placeholder="Search bills..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="ps-10"
                />
              </div>
            </div>

            {/* Content */}
            {isLoading ? (
              <LoadingSpinner />
            ) : !filteredBills?.length ? (
              <EmptyState
                icon={<Receipt className="h-12 w-12" />}
                title="No bills yet"
                description="Create your first purchase bill to start tracking expenses."
                action={
                  <Link href="/accounting/purchase-bills/new">
                    <Button>
                      <Plus className="h-4 w-4 me-2" />
                      New Bill
                    </Button>
                  </Link>
                }
              />
            ) : (
              <div className="space-y-2">
                {/* Header */}
                <div className="grid grid-cols-12 gap-4 px-4 py-2 text-sm font-medium text-muted-foreground border-b">
                  <div className="col-span-2">Bill #</div>
                  <div className="col-span-2">Date</div>
                  <div className="col-span-2">Vendor</div>
                  <div className="col-span-2">Vendor Ref</div>
                  <div className="col-span-2 text-end">Amount</div>
                  <div className="col-span-1">Status</div>
                  <div className="col-span-1"></div>
                </div>

                {/* Rows */}
                {filteredBills.map((bill) => (
                  <div
                    key={bill.id}
                    className="grid grid-cols-12 gap-4 px-4 py-3 rounded-lg border hover:bg-muted/50 transition-colors items-center"
                  >
                    <div className="col-span-2">
                      <Link
                        href={`/accounting/purchase-bills/${bill.id}`}
                        className="font-mono text-sm font-medium hover:text-primary hover:underline ltr-code"
                      >
                        {bill.bill_number}
                      </Link>
                    </div>
                    <div className="col-span-2 text-sm text-muted-foreground">
                      {formatDate(bill.bill_date)}
                    </div>
                    <div className="col-span-2">
                      <span className="font-medium truncate block">{bill.vendor_name}</span>
                      <p className="text-sm text-muted-foreground font-mono ltr-code">
                        {bill.vendor_code}
                      </p>
                    </div>
                    <div className="col-span-2 text-sm text-muted-foreground">
                      {bill.vendor_bill_reference || "—"}
                    </div>
                    <div className="col-span-2 text-end font-mono ltr-number font-medium">
                      {parseFloat(bill.total_amount).toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </div>
                    <div className="col-span-1">
                      <Badge className={cn("text-xs", BILL_STATUS_COLORS[bill.status])}>
                        {BILL_STATUS_LABELS[bill.status]}
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
                          <DropdownMenuItem onClick={() => router.push(`/accounting/purchase-bills/${bill.id}`)}>
                            <Eye className="h-4 w-4 me-2" />
                            View
                          </DropdownMenuItem>
                          {bill.status === "DRAFT" && (
                            <>
                              <DropdownMenuItem onClick={() => router.push(`/accounting/purchase-bills/${bill.id}/edit`)}>
                                <Pencil className="h-4 w-4 me-2" />
                                Edit
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem onClick={() => setPostDialog({ open: true, bill })}>
                                <Send className="h-4 w-4 me-2" />
                                Post Bill
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                onClick={() => setDeleteDialog({ open: true, bill })}
                                className="text-destructive"
                              >
                                <Trash2 className="h-4 w-4 me-2" />
                                Delete
                              </DropdownMenuItem>
                            </>
                          )}
                          {bill.status === "POSTED" && (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                onClick={() => setVoidDialog({ open: true, bill })}
                                className="text-destructive"
                              >
                                <XCircle className="h-4 w-4 me-2" />
                                Void Bill
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
      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, bill: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Bill</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete bill &quot;{deleteDialog.bill?.bill_number}&quot;? This action cannot be undone.
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
      <AlertDialog open={postDialog.open} onOpenChange={(open: boolean) => setPostDialog({ open, bill: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Post Bill</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to post bill &quot;{postDialog.bill?.bill_number}&quot;? This will create a journal entry
              and the bill cannot be edited afterwards.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePost}>
              Post Bill
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Void confirmation dialog */}
      <AlertDialog open={voidDialog.open} onOpenChange={(open: boolean) => setVoidDialog({ open, bill: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Void Bill</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to void bill &quot;{voidDialog.bill?.bill_number}&quot;? This will create a reversing
              journal entry to cancel the original posting.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleVoid} className="bg-destructive text-destructive-foreground">
              Void Bill
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
