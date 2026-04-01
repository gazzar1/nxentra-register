import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Truck, Mail, Phone, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState } from "@/components/common";
import { PaginatedTable } from "@/components/common/PaginatedTable";
import type { ColumnDef } from "@/components/common/PaginatedTable";
import { usePaginatedVendors, useDeleteVendor, useVendorBalances } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import type { Vendor } from "@/types/account";
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

export default function VendorsPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const deleteVendor = useDeleteVendor();
  const { data: balancesData } = useVendorBalances();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("code");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; vendor: Vendor | null }>({
    open: false,
    vendor: null,
  });

  const { data: response, isLoading } = usePaginatedVendors({
    search: search || undefined,
    page,
    page_size: pageSize,
    ordering,
  });

  const vendors = response?.results || [];
  const totalCount = response?.count || 0;
  const totalPages = response?.total_pages || 1;

  const balanceMap = new Map(
    balancesData?.balances?.map((b) => [b.vendor_code, b]) || []
  );

  const handleSearchChange = (value: string) => {
    setSearch(value);
    setPage(1);
  };

  const handleDelete = async () => {
    if (!deleteDialog.vendor) return;
    try {
      await deleteVendor.mutateAsync(deleteDialog.vendor.code);
      toast({ title: "Vendor deleted", description: `${deleteDialog.vendor.name} has been deleted.` });
    } catch (error) {
      toast({ title: "Error", description: "Failed to delete vendor.", variant: "destructive" });
    } finally {
      setDeleteDialog({ open: false, vendor: null });
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "ACTIVE": return <Badge variant="default" className="bg-green-500">Active</Badge>;
      case "INACTIVE": return <Badge variant="secondary">Inactive</Badge>;
      case "BLOCKED": return <Badge variant="destructive">Blocked</Badge>;
      default: return <Badge variant="outline">{status}</Badge>;
    }
  };

  const columns: ColumnDef<Vendor>[] = [
    {
      key: "code",
      label: "Code",
      sortable: true,
      render: (v) => <span className="font-mono text-sm ltr-code">{v.code}</span>,
    },
    {
      key: "name",
      label: "Name",
      sortable: true,
      render: (v) => (
        <div>
          <Link href={`/accounting/vendors/${v.code}`} className="font-medium hover:text-primary hover:underline">
            {v.name}
          </Link>
          {v.name_ar && <p className="text-sm text-muted-foreground" dir="rtl">{v.name_ar}</p>}
        </div>
      ),
    },
    {
      key: "balance",
      label: "Balance",
      className: "text-end",
      render: (v) => {
        const balance = balanceMap.get(v.code);
        const val = balance ? parseFloat(balance.balance) : 0;
        return (
          <span className={cn("font-mono text-sm ltr-number font-medium", val > 0 ? "text-red-600" : val < 0 ? "text-green-600" : "")}>
            {val.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        );
      },
    },
    {
      key: "email",
      label: "Contact",
      sortable: true,
      render: (v) => (
        <div className="text-sm">
          {v.email && <div className="flex items-center gap-1 text-muted-foreground"><Mail className="h-3 w-3" /><span className="truncate">{v.email}</span></div>}
          {v.phone && <div className="flex items-center gap-1 text-muted-foreground"><Phone className="h-3 w-3" /><span>{v.phone}</span></div>}
        </div>
      ),
    },
    {
      key: "default_ap_account_code",
      label: "AP Account",
      render: (v) => v.default_ap_account_code
        ? <span className="font-mono ltr-code text-muted-foreground text-sm">{v.default_ap_account_code}</span>
        : <span className="text-muted-foreground">—</span>,
    },
    {
      key: "payment_terms_days",
      label: "Terms",
      render: (v) => <span className="text-sm">{v.payment_terms_days} days</span>,
    },
    {
      key: "status",
      label: "Status",
      render: (v) => getStatusBadge(v.status),
    },
    {
      key: "actions",
      label: "",
      render: (v) => (
        <div className="flex items-center justify-end gap-1">
          <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); router.push(`/accounting/vendors/${v.code}/edit`); }}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setDeleteDialog({ open: true, vendor: v }); }}>
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Vendors (AP)"
          subtitle="Manage your accounts payable vendors"
          actions={
            <Link href="/accounting/vendors/new">
              <Button><Plus className="h-4 w-4 me-2" />Add Vendor</Button>
            </Link>
          }
        />
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input placeholder="Search vendors..." value={search} onChange={(e) => handleSearchChange(e.target.value)} className="ps-10" />
              </div>
            </div>
            <PaginatedTable
              data={vendors}
              columns={columns}
              keyExtractor={(v) => v.code}
              page={page}
              pageSize={pageSize}
              totalCount={totalCount}
              totalPages={totalPages}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              ordering={ordering}
              onOrderingChange={setOrdering}
              onRowClick={(v) => router.push(`/accounting/vendors/${v.code}`)}
              isLoading={isLoading}
              emptyState={
                <EmptyState
                  icon={<Truck className="h-12 w-12" />}
                  title="No vendors yet"
                  description="Add your first vendor to start tracking accounts payable."
                  action={<Link href="/accounting/vendors/new"><Button><Plus className="h-4 w-4 me-2" />Add Vendor</Button></Link>}
                />
              }
            />
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, vendor: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Vendor</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{deleteDialog.vendor?.name}&quot;? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
