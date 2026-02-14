import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Truck, Mail, Phone, Pencil, Trash2, Building2 } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useVendors, useDeleteVendor } from "@/queries/useAccounts";
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
  const { data: vendors, isLoading } = useVendors();
  const deleteVendor = useDeleteVendor();
  const [search, setSearch] = useState("");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; vendor: Vendor | null }>({
    open: false,
    vendor: null,
  });

  const filteredVendors = vendors?.filter((v) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      v.code.toLowerCase().includes(searchLower) ||
      v.name.toLowerCase().includes(searchLower) ||
      v.name_ar?.toLowerCase().includes(searchLower) ||
      v.email?.toLowerCase().includes(searchLower)
    );
  });

  const handleDelete = async () => {
    if (!deleteDialog.vendor) return;

    try {
      await deleteVendor.mutateAsync(deleteDialog.vendor.code);
      toast({
        title: "Vendor deleted",
        description: `${deleteDialog.vendor.name} has been deleted.`,
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete vendor.",
        variant: "destructive",
      });
    } finally {
      setDeleteDialog({ open: false, vendor: null });
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
          title="Vendors (AP)"
          subtitle="Manage your accounts payable vendors"
          actions={
            <Link href="/accounting/vendors/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />
                Add Vendor
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
                  placeholder="Search vendors..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="ps-10"
                />
              </div>
            </div>

            {/* Content */}
            {isLoading ? (
              <LoadingSpinner />
            ) : !filteredVendors?.length ? (
              <EmptyState
                icon={<Truck className="h-12 w-12" />}
                title="No vendors yet"
                description="Add your first vendor to start tracking accounts payable."
                action={
                  <Link href="/accounting/vendors/new">
                    <Button>
                      <Plus className="h-4 w-4 me-2" />
                      Add Vendor
                    </Button>
                  </Link>
                }
              />
            ) : (
              <div className="space-y-2">
                {/* Header */}
                <div className="grid grid-cols-12 gap-4 px-4 py-2 text-sm font-medium text-muted-foreground border-b">
                  <div className="col-span-2">Code</div>
                  <div className="col-span-3">Name</div>
                  <div className="col-span-2">Contact</div>
                  <div className="col-span-2">AP Account</div>
                  <div className="col-span-1">Terms</div>
                  <div className="col-span-1">Status</div>
                  <div className="col-span-1"></div>
                </div>

                {/* Rows */}
                {filteredVendors.map((vendor) => (
                  <div
                    key={vendor.code}
                    className="grid grid-cols-12 gap-4 px-4 py-3 rounded-lg border hover:bg-muted/50 transition-colors items-center"
                  >
                    <div className="col-span-2">
                      <span className="font-mono text-sm ltr-code">{vendor.code}</span>
                    </div>
                    <div className="col-span-3">
                      <Link
                        href={`/accounting/vendors/${vendor.code}`}
                        className="font-medium hover:text-primary hover:underline"
                      >
                        {vendor.name}
                      </Link>
                      {vendor.name_ar && (
                        <p className="text-sm text-muted-foreground" dir="rtl">
                          {vendor.name_ar}
                        </p>
                      )}
                    </div>
                    <div className="col-span-2 text-sm">
                      {vendor.email && (
                        <div className="flex items-center gap-1 text-muted-foreground">
                          <Mail className="h-3 w-3" />
                          <span className="truncate">{vendor.email}</span>
                        </div>
                      )}
                      {vendor.phone && (
                        <div className="flex items-center gap-1 text-muted-foreground">
                          <Phone className="h-3 w-3" />
                          <span>{vendor.phone}</span>
                        </div>
                      )}
                    </div>
                    <div className="col-span-2 text-sm">
                      {vendor.default_ap_account_code ? (
                        <span className="font-mono ltr-code text-muted-foreground">
                          {vendor.default_ap_account_code}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </div>
                    <div className="col-span-1 text-sm">
                      {vendor.payment_terms_days} days
                    </div>
                    <div className="col-span-1">
                      {getStatusBadge(vendor.status)}
                    </div>
                    <div className="col-span-1 flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => router.push(`/accounting/vendors/${vendor.code}/edit`)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteDialog({ open: true, vendor })}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Delete confirmation dialog */}
      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, vendor: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Vendor</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete "{deleteDialog.vendor?.name}"? This action cannot be undone.
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
