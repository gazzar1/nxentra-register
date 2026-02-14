import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, Search, Percent, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useTaxCodes, useDeleteTaxCode } from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import type { TaxCode, TaxDirection } from "@/types/sales";
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

const DIRECTION_LABELS: Record<TaxDirection, string> = {
  INPUT: "Input (Purchases)",
  OUTPUT: "Output (Sales)",
};

const DIRECTION_COLORS: Record<TaxDirection, string> = {
  INPUT: "bg-orange-100 text-orange-800",
  OUTPUT: "bg-blue-100 text-blue-800",
};

export default function TaxCodesPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { data: taxCodes, isLoading } = useTaxCodes();
  const deleteTaxCode = useDeleteTaxCode();
  const [search, setSearch] = useState("");
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; taxCode: TaxCode | null }>({
    open: false,
    taxCode: null,
  });

  const filteredTaxCodes = taxCodes?.filter((tc) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      tc.code.toLowerCase().includes(searchLower) ||
      tc.name.toLowerCase().includes(searchLower) ||
      tc.name_ar?.toLowerCase().includes(searchLower)
    );
  });

  const handleDelete = async () => {
    if (!deleteDialog.taxCode) return;

    try {
      await deleteTaxCode.mutateAsync(deleteDialog.taxCode.id);
      toast({
        title: "Tax code deleted",
        description: `${deleteDialog.taxCode.name} has been deleted.`,
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete tax code.",
        variant: "destructive",
      });
    } finally {
      setDeleteDialog({ open: false, taxCode: null });
    }
  };

  const formatRate = (rate: string) => {
    const percentage = parseFloat(rate) * 100;
    return `${percentage.toFixed(percentage % 1 === 0 ? 0 : 2)}%`;
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Tax Codes"
          subtitle="Manage tax rates for sales and purchases"
          actions={
            <Link href="/accounting/tax-codes/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />
                Add Tax Code
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
                  placeholder="Search tax codes..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="ps-10"
                />
              </div>
            </div>

            {/* Content */}
            {isLoading ? (
              <LoadingSpinner />
            ) : !filteredTaxCodes?.length ? (
              <EmptyState
                icon={<Percent className="h-12 w-12" />}
                title="No tax codes yet"
                description="Add tax codes to apply VAT and other taxes to your invoices and bills."
                action={
                  <Link href="/accounting/tax-codes/new">
                    <Button>
                      <Plus className="h-4 w-4 me-2" />
                      Add Tax Code
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
                  <div className="col-span-2">Rate</div>
                  <div className="col-span-2">Direction</div>
                  <div className="col-span-2">Tax Account</div>
                  <div className="col-span-1"></div>
                </div>

                {/* Rows */}
                {filteredTaxCodes.map((taxCode) => (
                  <div
                    key={taxCode.id}
                    className="grid grid-cols-12 gap-4 px-4 py-3 rounded-lg border hover:bg-muted/50 transition-colors items-center"
                  >
                    <div className="col-span-2">
                      <span className="font-mono text-sm ltr-code">{taxCode.code}</span>
                    </div>
                    <div className="col-span-3">
                      <span className="font-medium">{taxCode.name}</span>
                      {taxCode.name_ar && (
                        <p className="text-sm text-muted-foreground" dir="rtl">
                          {taxCode.name_ar}
                        </p>
                      )}
                    </div>
                    <div className="col-span-2 text-lg font-mono font-semibold ltr-number">
                      {formatRate(taxCode.rate)}
                    </div>
                    <div className="col-span-2">
                      <Badge className={cn("text-xs", DIRECTION_COLORS[taxCode.direction])}>
                        {DIRECTION_LABELS[taxCode.direction]}
                      </Badge>
                    </div>
                    <div className="col-span-2 text-sm">
                      {taxCode.tax_account_code ? (
                        <span className="font-mono ltr-code text-muted-foreground">
                          {taxCode.tax_account_code}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </div>
                    <div className="col-span-1 flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => router.push(`/accounting/tax-codes/${taxCode.id}/edit`)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteDialog({ open: true, taxCode })}
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
      <AlertDialog open={deleteDialog.open} onOpenChange={(open: boolean) => setDeleteDialog({ open, taxCode: null })}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Tax Code</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{deleteDialog.taxCode?.name}&quot;? This action cannot be undone.
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
