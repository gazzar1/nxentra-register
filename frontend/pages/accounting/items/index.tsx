import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Package, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useItems, useDeleteItem } from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { Item, ItemType } from "@/types/sales";
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

const ITEM_TYPE_LABELS: Record<ItemType, string> = {
  INVENTORY: "Inventory",
  SERVICE: "Service",
  NON_STOCK: "Non-Stock",
};

const ITEM_TYPE_COLORS: Record<ItemType, string> = {
  INVENTORY: "bg-blue-100 text-blue-800",
  SERVICE: "bg-purple-100 text-purple-800",
  NON_STOCK: "bg-orange-100 text-orange-800",
};

export default function ItemsPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: items, isLoading } = useItems();
  const deleteItem = useDeleteItem();
  const [search, setSearch] = useState("");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; item: Item | null }>({
    open: false,
    item: null,
  });

  const filteredItems = items?.filter((i) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      i.code.toLowerCase().includes(searchLower) ||
      i.name.toLowerCase().includes(searchLower) ||
      i.name_ar?.toLowerCase().includes(searchLower)
    );
  });

  const handleDelete = async () => {
    if (!deleteDialog.item) return;

    try {
      await deleteItem.mutateAsync(deleteDialog.item.id);
      toast({
        title: "Item deleted",
        description: `${deleteDialog.item.name} has been deleted.`,
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete item.",
        variant: "destructive",
      });
    } finally {
      setDeleteDialog({ open: false, item: null });
    }
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Items"
          subtitle="Manage your product and service catalog"
          actions={
            <Link href="/accounting/items/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />
                Add Item
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
                  placeholder="Search items..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="ps-10"
                />
              </div>
            </div>

            {/* Content */}
            {isLoading ? (
              <LoadingSpinner />
            ) : !filteredItems?.length ? (
              <EmptyState
                icon={<Package className="h-12 w-12" />}
                title="No items yet"
                description="Add your first item to start managing your product and service catalog."
                action={
                  <Link href="/accounting/items/new">
                    <Button>
                      <Plus className="h-4 w-4 me-2" />
                      Add Item
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
                  <div className="col-span-1">Type</div>
                  <div className="col-span-2">Sales Account</div>
                  <div className="col-span-2">Unit Price</div>
                  <div className="col-span-1">Status</div>
                  <div className="col-span-1"></div>
                </div>

                {/* Rows */}
                {filteredItems.map((item) => (
                  <div
                    key={item.id}
                    className="grid grid-cols-12 gap-4 px-4 py-3 rounded-lg border hover:bg-muted/50 transition-colors items-center"
                  >
                    <div className="col-span-2">
                      <span className="font-mono text-sm ltr-code">{item.code}</span>
                    </div>
                    <div className="col-span-3">
                      <Link
                        href={`/accounting/items/${item.id}`}
                        className="font-medium hover:text-primary hover:underline"
                      >
                        {item.name}
                      </Link>
                      {item.name_ar && (
                        <p className="text-sm text-muted-foreground" dir="rtl">
                          {item.name_ar}
                        </p>
                      )}
                    </div>
                    <div className="col-span-1">
                      <Badge className={cn("text-xs", ITEM_TYPE_COLORS[item.item_type])}>
                        {ITEM_TYPE_LABELS[item.item_type]}
                      </Badge>
                    </div>
                    <div className="col-span-2 text-sm">
                      {item.sales_account_code ? (
                        <span className="font-mono ltr-code text-muted-foreground">
                          {item.sales_account_code}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </div>
                    <div className="col-span-2 text-sm font-mono ltr-number">
                      {parseFloat(item.default_unit_price).toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </div>
                    <div className="col-span-1">
                      {item.is_active ? (
                        <Badge variant="default" className="bg-green-500">Active</Badge>
                      ) : (
                        <Badge variant="secondary">Inactive</Badge>
                      )}
                    </div>
                    <div className="col-span-1 flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => router.push(`/accounting/items/${item.id}/edit`)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteDialog({ open: true, item })}
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
      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, item: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Item</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete "{deleteDialog.item?.name}"? This action cannot be undone.
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
