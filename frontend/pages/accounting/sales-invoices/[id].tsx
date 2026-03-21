import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, Pencil, Send, XCircle, FileText, Printer, Download } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useSalesInvoice, usePostSalesInvoice, useVoidSalesInvoice } from "@/queries/useSales";
import { useToast } from "@/components/ui/toaster";
import { INVOICE_STATUS_COLORS, INVOICE_STATUS_LABELS } from "@/types/sales";
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

export default function SalesInvoiceDetailPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { id } = router.query;
  const { toast } = useToast();
  const { data: invoice, isLoading } = useSalesInvoice(parseInt(id as string));
  const postInvoice = usePostSalesInvoice();
  const voidInvoice = useVoidSalesInvoice();
  const [postDialog, setPostDialog] = useState(false);
  const [voidDialog, setVoidDialog] = useState(false);

  const handlePost = async () => {
    if (!invoice) return;

    try {
      await postInvoice.mutateAsync(invoice.id);
      toast({
        title: "Invoice posted",
        description: `Invoice ${invoice.invoice_number} has been posted to the general ledger.`,
      });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to post invoice.",
        variant: "destructive",
      });
    } finally {
      setPostDialog(false);
    }
  };

  const handleVoid = async () => {
    if (!invoice) return;

    try {
      await voidInvoice.mutateAsync({ id: invoice.id });
      toast({
        title: "Invoice voided",
        description: `Invoice ${invoice.invoice_number} has been voided with a reversing entry.`,
      });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.response?.data?.detail || "Failed to void invoice.",
        variant: "destructive",
      });
    } finally {
      setVoidDialog(false);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "—";
    return new Date(dateStr).toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  };

  if (isLoading) {
    return (
      <AppLayout>
        <LoadingSpinner />
      </AppLayout>
    );
  }

  if (!invoice) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <p className="text-muted-foreground">Invoice not found</p>
          <Link href="/accounting/sales-invoices">
            <Button variant="link">Back to invoices</Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={
            <div className="flex items-center gap-3">
              <span>{invoice.invoice_number}</span>
              <Badge className={cn("text-sm", INVOICE_STATUS_COLORS[invoice.status])}>
                {INVOICE_STATUS_LABELS[invoice.status]}
              </Badge>
            </div>
          }
          subtitle={`Sales invoice for ${invoice.customer_name}`}
          actions={
            <div className="flex gap-2">
              <Link href="/accounting/sales-invoices">
                <Button variant="outline">
                  <ArrowLeft className="h-4 w-4 me-2" />
                  Back
                </Button>
              </Link>
              <Button
                variant="outline"
                onClick={() => window.open(`/accounting/sales-invoices/${invoice.id}/print`, '_blank')}
              >
                <Printer className="h-4 w-4 me-2" />
                Print
              </Button>
              <Button
                variant="outline"
                onClick={async () => {
                  const { getAccessToken } = await import("@/lib/auth-storage");
                  const token = getAccessToken();
                  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
                  const res = await fetch(`${apiUrl}/sales/invoices/${invoice.id}/pdf/`, {
                    headers: { Authorization: `Bearer ${token}` },
                  });
                  if (!res.ok) { toast({ title: "PDF generation failed", variant: "destructive" }); return; }
                  const blob = await res.blob();
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `${invoice.invoice_number}.pdf`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                <Download className="h-4 w-4 me-2" />
                Download PDF
              </Button>
              {invoice.status === "DRAFT" && (
                <>
                  <Link href={`/accounting/sales-invoices/${invoice.id}/edit`}>
                    <Button variant="outline">
                      <Pencil className="h-4 w-4 me-2" />
                      Edit
                    </Button>
                  </Link>
                  <Button onClick={() => setPostDialog(true)}>
                    <Send className="h-4 w-4 me-2" />
                    Post Invoice
                  </Button>
                </>
              )}
              {invoice.status === "POSTED" && (
                <Button variant="destructive" onClick={() => setVoidDialog(true)}>
                  <XCircle className="h-4 w-4 me-2" />
                  Void Invoice
                </Button>
              )}
            </div>
          }
        />

        {/* Invoice Details */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Invoice Details</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Invoice Date</p>
                  <p className="font-medium">{formatDate(invoice.invoice_date)}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Due Date</p>
                  <p className="font-medium">{formatDate(invoice.due_date)}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Posting Profile</p>
                  <p className="font-medium">{invoice.posting_profile_code}</p>
                </div>
                {invoice.posted_at && (
                  <div>
                    <p className="text-sm text-muted-foreground">Posted At</p>
                    <p className="font-medium">{formatDate(invoice.posted_at)}</p>
                  </div>
                )}
              </div>

              {invoice.posted_journal_entry && (
                <div className="mt-4 p-3 bg-muted rounded-lg">
                  <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm text-muted-foreground">Journal Entry:</span>
                    <Link
                      href={`/accounting/journal-entries/${invoice.posted_journal_entry}`}
                      className="text-sm font-mono text-primary hover:underline"
                    >
                      {invoice.posted_journal_entry_number || `#${invoice.posted_journal_entry}`}
                    </Link>
                  </div>
                </div>
              )}

              {invoice.notes && (
                <div className="mt-4">
                  <p className="text-sm text-muted-foreground">Notes</p>
                  <p className="text-sm mt-1">{invoice.notes}</p>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Customer</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="font-medium">{invoice.customer_name}</p>
              <p className="text-sm text-muted-foreground font-mono">{invoice.customer_code}</p>
            </CardContent>
          </Card>
        </div>

        {/* Line Items */}
        <Card>
          <CardHeader>
            <CardTitle>Line Items</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b text-sm text-muted-foreground">
                    <th className="text-start py-2 px-2 w-[50px]">#</th>
                    <th className="text-start py-2 px-2">Description</th>
                    <th className="text-start py-2 px-2 w-[100px]">Account</th>
                    <th className="text-end py-2 px-2 w-[80px]">Qty</th>
                    <th className="text-end py-2 px-2 w-[100px]">Unit Price</th>
                    <th className="text-end py-2 px-2 w-[100px]">Gross</th>
                    <th className="text-end py-2 px-2 w-[80px]">Discount</th>
                    <th className="text-end py-2 px-2 w-[100px]">Net</th>
                    <th className="text-end py-2 px-2 w-[80px]">Tax</th>
                    <th className="text-end py-2 px-2 w-[100px]">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {invoice.lines.map((line) => (
                    <tr key={line.id} className="border-b">
                      <td className="py-2 px-2 text-muted-foreground">{line.line_number}</td>
                      <td className="py-2 px-2">
                        <p className="font-medium">{line.description}</p>
                        {line.item_code && (
                          <p className="text-xs text-muted-foreground font-mono">{line.item_code}</p>
                        )}
                      </td>
                      <td className="py-2 px-2 font-mono text-sm text-muted-foreground">
                        {line.account_code}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm">
                        {parseFloat(line.quantity).toLocaleString()}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm">
                        {parseFloat(line.unit_price).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm">
                        {parseFloat(line.gross_amount).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm text-red-600">
                        {parseFloat(line.discount_amount) > 0 ? (
                          `-${parseFloat(line.discount_amount).toLocaleString(undefined, {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}`
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm">
                        {parseFloat(line.net_amount).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm">
                        {parseFloat(line.tax_amount).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                        {line.tax_code_code && (
                          <span className="text-xs text-muted-foreground block">
                            {line.tax_code_code}
                          </span>
                        )}
                      </td>
                      <td className="py-2 px-2 text-end font-mono text-sm font-medium">
                        {parseFloat(line.line_total).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Totals */}
            <div className="flex justify-end mt-6">
              <div className="w-72 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Subtotal</span>
                  <span className="font-mono">
                    {parseFloat(invoice.subtotal).toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Total Discount</span>
                  <span className="font-mono text-red-600">
                    -{parseFloat(invoice.total_discount).toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Total Tax</span>
                  <span className="font-mono">
                    {parseFloat(invoice.total_tax).toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                </div>
                <div className="border-t pt-2 flex justify-between text-lg font-semibold">
                  <span>Total Amount</span>
                  <span className="font-mono">
                    {parseFloat(invoice.total_amount).toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Post confirmation dialog */}
      <AlertDialog open={postDialog} onOpenChange={setPostDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Post Invoice</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to post invoice &quot;{invoice.invoice_number}&quot;? This will create a journal entry
              and the invoice cannot be edited afterwards.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handlePost}>
              Post Invoice
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Void confirmation dialog */}
      <AlertDialog open={voidDialog} onOpenChange={setVoidDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Void Invoice</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to void invoice &quot;{invoice.invoice_number}&quot;? This will create a reversing
              journal entry to cancel the original posting.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleVoid} className="bg-destructive text-destructive-foreground">
              Void Invoice
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
