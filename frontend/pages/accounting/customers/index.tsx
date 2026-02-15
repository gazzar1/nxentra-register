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
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useCustomers, useDeleteCustomer, useCustomerBalances } from "@/queries/useAccounts";
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
  const { data: customers, isLoading } = useCustomers();
  const { data: balancesData } = useCustomerBalances();
  const deleteCustomer = useDeleteCustomer();

  // Create a map of customer code to balance for quick lookup
  const balanceMap = new Map(
    balancesData?.balances?.map((b) => [b.customer_code, b]) || []
  );
  const [search, setSearch] = useState("");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; customer: Customer | null }>({
    open: false,
    customer: null,
  });

  const filteredCustomers = customers?.filter((c) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      c.code.toLowerCase().includes(searchLower) ||
      c.name.toLowerCase().includes(searchLower) ||
      c.name_ar?.toLowerCase().includes(searchLower) ||
      c.email?.toLowerCase().includes(searchLower)
    );
  });

  const handleDelete = async () => {
    if (!deleteDialog.customer) return;

    try {
      await deleteCustomer.mutateAsync(deleteDialog.customer.code);
      toast({
        title: "Customer deleted",
        description: `${deleteDialog.customer.name} has been deleted.`,
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete customer.",
        variant: "destructive",
      });
    } finally {
      setDeleteDialog({ open: false, customer: null });
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "ACTIVE":
        return <Badge variant="default" className="bg-green-500">Active</Badge>;
      case "INACTIVE":
        return <Badge variant="secondary">Inactive</Badge>;
      case "BLOCKED":
        return <Badge variant="destructive">Blocked</Badge>;
      default:
        return <Badge variant="outline">{status}</Badge>;
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Customers (AR)"
          subtitle="Manage your accounts receivable customers"
          actions={
            <Link href="/accounting/customers/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />
                Add Customer
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
                  placeholder="Search customers..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="ps-10"
                />
              </div>
            </div>

            {/* Content */}
            {isLoading ? (
              <LoadingSpinner />
            ) : !filteredCustomers?.length ? (
              <EmptyState
                icon={<UserCircle className="h-12 w-12" />}
                title="No customers yet"
                description="Add your first customer to start tracking accounts receivable."
                action={
                  <Link href="/accounting/customers/new">
                    <Button>
                      <Plus className="h-4 w-4 me-2" />
                      Add Customer
                    </Button>
                  </Link>
                }
              />
            ) : (
              <div className="space-y-2">
                {/* Header */}
                <div className="grid grid-cols-12 gap-4 px-4 py-2 text-sm font-medium text-muted-foreground border-b">
                  <div className="col-span-1">Code</div>
                  <div className="col-span-2">Name</div>
                  <div className="col-span-2 text-end">Balance</div>
                  <div className="col-span-2">Contact</div>
                  <div className="col-span-2">AR Account</div>
                  <div className="col-span-1">Credit Limit</div>
                  <div className="col-span-1">Status</div>
                  <div className="col-span-1"></div>
                </div>

                {/* Rows */}
                {filteredCustomers.map((customer) => {
                  const balance = balanceMap.get(customer.code);
                  const balanceValue = balance ? parseFloat(balance.balance) : 0;
                  return (
                    <div
                      key={customer.code}
                      className="grid grid-cols-12 gap-4 px-4 py-3 rounded-lg border hover:bg-muted/50 transition-colors items-center"
                    >
                      <div className="col-span-1">
                        <span className="font-mono text-sm ltr-code">{customer.code}</span>
                      </div>
                      <div className="col-span-2">
                        <Link
                          href={`/accounting/customers/${customer.code}`}
                          className="font-medium hover:text-primary hover:underline"
                        >
                          {customer.name}
                        </Link>
                        {customer.name_ar && (
                          <p className="text-sm text-muted-foreground" dir="rtl">
                            {customer.name_ar}
                          </p>
                        )}
                      </div>
                      <div className="col-span-2 text-end">
                        <span className={cn(
                          "font-mono text-sm ltr-number font-medium",
                          balanceValue > 0 ? "text-green-600" : balanceValue < 0 ? "text-red-600" : ""
                        )}>
                          {balanceValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </span>
                      </div>
                      <div className="col-span-2 text-sm">
                        {customer.email && (
                          <div className="flex items-center gap-1 text-muted-foreground">
                            <Mail className="h-3 w-3" />
                            <span className="truncate">{customer.email}</span>
                          </div>
                        )}
                        {customer.phone && (
                          <div className="flex items-center gap-1 text-muted-foreground">
                            <Phone className="h-3 w-3" />
                            <span>{customer.phone}</span>
                          </div>
                        )}
                      </div>
                      <div className="col-span-2 text-sm">
                        {customer.default_ar_account_code ? (
                          <span className="font-mono ltr-code text-muted-foreground">
                            {customer.default_ar_account_code}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </div>
                      <div className="col-span-1 text-sm font-mono ltr-number">
                        {customer.credit_limit || "—"}
                      </div>
                      <div className="col-span-1">
                        {getStatusBadge(customer.status)}
                      </div>
                      <div className="col-span-1 flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => router.push(`/accounting/customers/${customer.code}/edit`)}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleteDialog({ open: true, customer })}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Delete confirmation dialog */}
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
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground">
              Delete
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
