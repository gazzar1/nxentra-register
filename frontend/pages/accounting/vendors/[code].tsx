import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import { ArrowLeft, Pencil, Trash2, Mail, Phone, MapPin, Building2, CreditCard, Landmark } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useVendor, useDeleteVendor } from "@/queries/useAccounts";
import { useToast } from "@/components/ui/toaster";
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

export default function VendorDetailPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const code = router.query.code as string;
  const { data: vendor, isLoading } = useVendor(code);
  const deleteVendor = useDeleteVendor();
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const handleDelete = async () => {
    if (!vendor) return;

    try {
      await deleteVendor.mutateAsync(vendor.code);
      toast({
        title: "Vendor deleted",
        description: `${vendor.name} has been deleted.`,
      });
      router.push("/accounting/vendors");
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete vendor.",
        variant: "destructive",
      });
    } finally {
      setDeleteDialogOpen(false);
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

  if (isLoading) {
    return (
      <AppLayout>
        <LoadingSpinner />
      </AppLayout>
    );
  }

  if (!vendor) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <h2 className="text-lg font-semibold">Vendor not found</h2>
          <p className="text-muted-foreground mt-2">The vendor you&apos;re looking for doesn&apos;t exist.</p>
          <Link href="/accounting/vendors">
            <Button className="mt-4">Back to Vendors</Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={vendor.name}
          subtitle={
            <div className="flex items-center gap-2 mt-1">
              <span className="font-mono text-sm ltr-code">{vendor.code}</span>
              {getStatusBadge(vendor.status)}
            </div>
          }
          actions={
            <div className="flex items-center gap-2">
              <Link href="/accounting/vendors">
                <Button variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />
                  Back
                </Button>
              </Link>
              <Link href={`/accounting/vendors/${vendor.code}/edit`}>
                <Button variant="outline">
                  <Pencil className="h-4 w-4 me-2" />
                  Edit
                </Button>
              </Link>
              <Button variant="destructive" onClick={() => setDeleteDialogOpen(true)}>
                <Trash2 className="h-4 w-4 me-2" />
                Delete
              </Button>
            </div>
          }
        />

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Basic Information */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Building2 className="h-5 w-5" />
                Basic Information
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Code</p>
                  <p className="font-mono ltr-code">{vendor.code}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Status</p>
                  <p>{getStatusBadge(vendor.status)}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Name (English)</p>
                  <p className="font-medium">{vendor.name}</p>
                </div>
                {vendor.name_ar && (
                  <div>
                    <p className="text-sm text-muted-foreground">Name (Arabic)</p>
                    <p className="font-medium" dir="rtl">{vendor.name_ar}</p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Contact Information */}
          <Card>
            <CardHeader>
              <CardTitle>Contact Information</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {vendor.email && (
                <div className="flex items-center gap-2">
                  <Mail className="h-4 w-4 text-muted-foreground" />
                  <a href={`mailto:${vendor.email}`} className="hover:underline">
                    {vendor.email}
                  </a>
                </div>
              )}
              {vendor.phone && (
                <div className="flex items-center gap-2">
                  <Phone className="h-4 w-4 text-muted-foreground" />
                  <a href={`tel:${vendor.phone}`} className="hover:underline">
                    {vendor.phone}
                  </a>
                </div>
              )}
              {vendor.address && (
                <div className="flex items-start gap-2">
                  <MapPin className="h-4 w-4 text-muted-foreground mt-1" />
                  <p className="whitespace-pre-wrap">{vendor.address}</p>
                </div>
              )}
              {!vendor.email && !vendor.phone && !vendor.address && (
                <p className="text-muted-foreground">No contact information provided</p>
              )}
            </CardContent>
          </Card>

          {/* Accounting Details */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CreditCard className="h-5 w-5" />
                Accounting Details
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Default AP Account</p>
                  <p className="font-mono ltr-code">
                    {vendor.default_ap_account_code || "Company default"}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Currency</p>
                  <p>{vendor.currency || "USD"}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Payment Terms</p>
                  <p>{vendor.payment_terms_days} days</p>
                </div>
                {vendor.tax_id && (
                  <div>
                    <p className="text-sm text-muted-foreground">Tax ID / VAT</p>
                    <p className="font-mono">{vendor.tax_id}</p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Bank Details */}
          {(vendor.bank_name || vendor.bank_account || vendor.bank_iban || vendor.bank_swift) && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Landmark className="h-5 w-5" />
                  Bank Details
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  {vendor.bank_name && (
                    <div>
                      <p className="text-sm text-muted-foreground">Bank Name</p>
                      <p>{vendor.bank_name}</p>
                    </div>
                  )}
                  {vendor.bank_account && (
                    <div>
                      <p className="text-sm text-muted-foreground">Account Number</p>
                      <p className="font-mono">{vendor.bank_account}</p>
                    </div>
                  )}
                  {vendor.bank_iban && (
                    <div>
                      <p className="text-sm text-muted-foreground">IBAN</p>
                      <p className="font-mono">{vendor.bank_iban}</p>
                    </div>
                  )}
                  {vendor.bank_swift && (
                    <div>
                      <p className="text-sm text-muted-foreground">SWIFT/BIC</p>
                      <p className="font-mono">{vendor.bank_swift}</p>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Notes */}
          {(vendor.notes || vendor.notes_ar) && (
            <Card>
              <CardHeader>
                <CardTitle>Notes</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {vendor.notes && (
                  <div>
                    <p className="text-sm text-muted-foreground mb-1">Notes (English)</p>
                    <p className="whitespace-pre-wrap">{vendor.notes}</p>
                  </div>
                )}
                {vendor.notes_ar && (
                  <div>
                    <p className="text-sm text-muted-foreground mb-1">Notes (Arabic)</p>
                    <p className="whitespace-pre-wrap" dir="rtl">{vendor.notes_ar}</p>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* Delete confirmation dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Vendor</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{vendor.name}&quot;? This action cannot be undone.
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
