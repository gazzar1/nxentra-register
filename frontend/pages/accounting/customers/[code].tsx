import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import { ArrowLeft, Pencil, Trash2, Mail, Phone, MapPin, Building2, CreditCard, Calendar } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useCustomer, useDeleteCustomer } from "@/queries/useAccounts";
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

export default function CustomerDetailPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const code = router.query.code as string;
  const { data: customer, isLoading } = useCustomer(code);
  const deleteCustomer = useDeleteCustomer();
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const handleDelete = async () => {
    if (!customer) return;

    try {
      await deleteCustomer.mutateAsync(customer.code);
      toast({
        title: "Customer deleted",
        description: `${customer.name} has been deleted.`,
      });
      router.push("/accounting/customers");
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete customer.",
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

  if (!customer) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <h2 className="text-lg font-semibold">Customer not found</h2>
          <p className="text-muted-foreground mt-2">The customer you&apos;re looking for doesn&apos;t exist.</p>
          <Link href="/accounting/customers">
            <Button className="mt-4">Back to Customers</Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={customer.name}
          subtitle={
            <div className="flex items-center gap-2 mt-1">
              <span className="font-mono text-sm ltr-code">{customer.code}</span>
              {getStatusBadge(customer.status)}
            </div>
          }
          actions={
            <div className="flex items-center gap-2">
              <Link href="/accounting/customers">
                <Button variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />
                  Back
                </Button>
              </Link>
              <Link href={`/accounting/customers/${customer.code}/edit`}>
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
                  <p className="font-mono ltr-code">{customer.code}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Status</p>
                  <p>{getStatusBadge(customer.status)}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Name (English)</p>
                  <p className="font-medium">{customer.name}</p>
                </div>
                {customer.name_ar && (
                  <div>
                    <p className="text-sm text-muted-foreground">Name (Arabic)</p>
                    <p className="font-medium" dir="rtl">{customer.name_ar}</p>
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
              {customer.email && (
                <div className="flex items-center gap-2">
                  <Mail className="h-4 w-4 text-muted-foreground" />
                  <a href={`mailto:${customer.email}`} className="hover:underline">
                    {customer.email}
                  </a>
                </div>
              )}
              {customer.phone && (
                <div className="flex items-center gap-2">
                  <Phone className="h-4 w-4 text-muted-foreground" />
                  <a href={`tel:${customer.phone}`} className="hover:underline">
                    {customer.phone}
                  </a>
                </div>
              )}
              {customer.address && (
                <div className="flex items-start gap-2">
                  <MapPin className="h-4 w-4 text-muted-foreground mt-1" />
                  <p className="whitespace-pre-wrap">{customer.address}</p>
                </div>
              )}
              {!customer.email && !customer.phone && !customer.address && (
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
                  <p className="text-sm text-muted-foreground">Default AR Account</p>
                  <p className="font-mono ltr-code">
                    {customer.default_ar_account_code || "Company default"}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Currency</p>
                  <p>{customer.currency || "USD"}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Credit Limit</p>
                  <p className="font-mono ltr-number">
                    {customer.credit_limit ? `${customer.currency || "USD"} ${customer.credit_limit}` : "No limit"}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Payment Terms</p>
                  <p>{customer.payment_terms_days} days</p>
                </div>
                {customer.tax_id && (
                  <div>
                    <p className="text-sm text-muted-foreground">Tax ID / VAT</p>
                    <p className="font-mono">{customer.tax_id}</p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Notes */}
          {(customer.notes || customer.notes_ar) && (
            <Card>
              <CardHeader>
                <CardTitle>Notes</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {customer.notes && (
                  <div>
                    <p className="text-sm text-muted-foreground mb-1">Notes (English)</p>
                    <p className="whitespace-pre-wrap">{customer.notes}</p>
                  </div>
                )}
                {customer.notes_ar && (
                  <div>
                    <p className="text-sm text-muted-foreground mb-1">Notes (Arabic)</p>
                    <p className="whitespace-pre-wrap" dir="rtl">{customer.notes_ar}</p>
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
            <AlertDialogTitle>Delete Customer</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{customer.name}&quot;? This action cannot be undone.
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
