import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, UserCircle, Mail, Phone, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState } from "@/components/common";
import { PaginatedTable } from "@/components/common/PaginatedTable";
import type { ColumnDef } from "@/components/common/PaginatedTable";
import { usePaginatedCustomers, useDeleteCustomer, useCustomerBalances } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
import type { Customer } from "@/types/account";
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

export default function CustomersPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const deleteCustomer = useDeleteCustomer();
  const { data: balancesData } = useCustomerBalances();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("code");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; customer: Customer | null }>({
    open: false,
    customer: null,
  });

  const { data: response, isLoading } = usePaginatedCustomers({
    search: search || undefined,
    page,
    page_size: pageSize,
    ordering,
  });

  const customers = response?.results || [];
  const totalCount = response?.count || 0;
  const totalPages = response?.total_pages || 1;

  const balanceMap = new Map(
    balancesData?.balances?.map((b) => [b.customer_code, b]) || []
  );

  const handleSearchChange = (value: string) => {
    setSearch(value);
    setPage(1);
  };

  const handleDelete = async () => {
    if (!deleteDialog.customer) return;
    try {
      await deleteCustomer.mutateAsync(deleteDialog.customer.code);
      toast({ title: "Customer deleted", description: `${deleteDialog.customer.name} has been deleted.` });
    } catch (error) {
      toast({ title: "Error", description: "Failed to delete customer.", variant: "destructive" });
    } finally {
      setDeleteDialog({ open: false, customer: null });
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

  const columns: ColumnDef<Customer>[] = [
    {
      key: "code",
      label: "Code",
      sortable: true,
      render: (c) => <span className="font-mono text-sm ltr-code">{c.code}</span>,
    },
    {
      key: "name",
      label: "Name",
      sortable: true,
      render: (c) => (
        <div>
          <Link href={`/accounting/customers/${c.code}`} className="font-medium hover:text-primary hover:underline">
            {c.name}
          </Link>
          {c.name_ar && <p className="text-sm text-muted-foreground" dir="rtl">{c.name_ar}</p>}
        </div>
      ),
    },
    {
      key: "balance",
      label: "Balance",
      className: "text-end",
      render: (c) => {
        const balance = balanceMap.get(c.code);
        const val = balance ? parseFloat(balance.balance) : 0;
        return (
          <span className={cn("font-mono text-sm ltr-number font-medium", val > 0 ? "text-green-600" : val < 0 ? "text-red-600" : "")}>
            {val.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        );
      },
    },
    {
      key: "email",
      label: "Contact",
      sortable: true,
      render: (c) => (
        <div className="text-sm">
          {c.email && <div className="flex items-center gap-1 text-muted-foreground"><Mail className="h-3 w-3" /><span className="truncate">{c.email}</span></div>}
          {c.phone && <div className="flex items-center gap-1 text-muted-foreground"><Phone className="h-3 w-3" /><span>{c.phone}</span></div>}
        </div>
      ),
    },
    {
      key: "default_ar_account_code",
      label: "AR Account",
      render: (c) => c.default_ar_account_code
        ? <span className="font-mono ltr-code text-muted-foreground text-sm">{c.default_ar_account_code}</span>
        : <span className="text-muted-foreground">—</span>,
    },
    {
      key: "status",
      label: "Status",
      render: (c) => getStatusBadge(c.status),
    },
    {
      key: "actions",
      label: "",
      render: (c) => (
        <div className="flex items-center justify-end gap-1">
          <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); router.push(`/accounting/customers/${c.code}/edit`); }}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setDeleteDialog({ open: true, customer: c }); }}>
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
          title="Customers (AR)"
          subtitle="Manage your accounts receivable customers"
          actions={
            <Link href="/accounting/customers/new">
              <Button><Plus className="h-4 w-4 me-2" />Add Customer</Button>
            </Link>
          }
        />
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input placeholder="Search customers..." value={search} onChange={(e) => handleSearchChange(e.target.value)} className="ps-10" />
              </div>
            </div>
            <PaginatedTable
              data={customers}
              columns={columns}
              keyExtractor={(c) => c.code}
              page={page}
              pageSize={pageSize}
              totalCount={totalCount}
              totalPages={totalPages}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              ordering={ordering}
              onOrderingChange={setOrdering}
              onRowClick={(c) => router.push(`/accounting/customers/${c.code}`)}
              isLoading={isLoading}
              emptyState={
                <EmptyState
                  icon={<UserCircle className="h-12 w-12" />}
                  title="No customers yet"
                  description="Add your first customer to start tracking accounts receivable."
                  action={<Link href="/accounting/customers/new"><Button><Plus className="h-4 w-4 me-2" />Add Customer</Button></Link>}
                />
              }
            />
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, customer: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Customer</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{deleteDialog.customer?.name}&quot;? This action cannot be undone.
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
