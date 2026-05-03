import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { ArrowLeft, FileText, Receipt } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useCreditNote } from "@/queries/useSales";
import {
  CREDIT_NOTE_STATUS_COLORS,
  CREDIT_NOTE_STATUS_LABELS,
  CREDIT_NOTE_REASON_LABELS,
} from "@/types/sales";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

export default function CreditNoteDetailPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { id } = router.query;
  const { formatDate } = useCompanyFormat();

  const numericId = parseInt(id as string, 10);
  const validId = !Number.isNaN(numericId);

  const { data: creditNote, isLoading } = useCreditNote(validId ? numericId : 0);

  if (!validId) {
    return (
      <AppLayout>
        <PageHeader title="Credit Note" subtitle="Invalid credit note ID." />
        <Button variant="outline" onClick={() => router.push("/accounting/credit-notes")}>
          <ArrowLeft className="ltr:mr-2 rtl:ml-2 h-4 w-4" />
          Back to Credit Notes
        </Button>
      </AppLayout>
    );
  }

  if (isLoading) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center min-h-[40vh]">
          <LoadingSpinner />
        </div>
      </AppLayout>
    );
  }

  if (!creditNote) {
    return (
      <AppLayout>
        <PageHeader title="Credit Note Not Found" subtitle="This credit note does not exist or you do not have access." />
        <Button variant="outline" onClick={() => router.push("/accounting/credit-notes")}>
          <ArrowLeft className="ltr:mr-2 rtl:ml-2 h-4 w-4" />
          Back to Credit Notes
        </Button>
      </AppLayout>
    );
  }

  const statusLabel = CREDIT_NOTE_STATUS_LABELS[creditNote.status as keyof typeof CREDIT_NOTE_STATUS_LABELS] || creditNote.status;
  const statusColor = CREDIT_NOTE_STATUS_COLORS[creditNote.status as keyof typeof CREDIT_NOTE_STATUS_COLORS] || "default";
  const reasonLabel = CREDIT_NOTE_REASON_LABELS[creditNote.reason as keyof typeof CREDIT_NOTE_REASON_LABELS] || creditNote.reason;

  return (
    <AppLayout>
      <div className="flex items-center justify-between mb-4">
        <Button variant="ghost" size="sm" onClick={() => router.push("/accounting/credit-notes")}>
          <ArrowLeft className="ltr:mr-2 rtl:ml-2 h-4 w-4" />
          Back
        </Button>
      </div>

      <PageHeader
        title={creditNote.credit_note_number || `CN-${creditNote.id}`}
        subtitle={`Credit Note · ${creditNote.customer_name || "—"}`}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Status</CardTitle>
          </CardHeader>
          <CardContent>
            <Badge variant={statusColor as any}>{statusLabel}</Badge>
            <p className="text-xs text-muted-foreground mt-2">{reasonLabel}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Total Amount</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold ltr-code">
              {creditNote.currency} {Number(creditNote.total_amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </p>
            <p className="text-xs text-muted-foreground mt-1">{formatDate(creditNote.credit_note_date)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Original Invoice</CardTitle>
          </CardHeader>
          <CardContent>
            {creditNote.invoice ? (
              <Link
                href={`/accounting/sales-invoices/${creditNote.invoice}`}
                className="flex items-center font-mono text-sm hover:text-primary hover:underline ltr-code"
              >
                <Receipt className="ltr:mr-2 rtl:ml-2 h-4 w-4" />
                {creditNote.invoice_number || `INV-${creditNote.invoice}`}
              </Link>
            ) : (
              <span className="text-sm text-muted-foreground">—</span>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Lines</CardTitle>
        </CardHeader>
        <CardContent>
          {creditNote.lines && creditNote.lines.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">Description</th>
                    <th className="py-2 pr-4 font-medium text-right">Quantity</th>
                    <th className="py-2 pr-4 font-medium text-right">Unit Price</th>
                    <th className="py-2 font-medium text-right">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {creditNote.lines.map((line: any) => (
                    <tr key={line.id} className="border-b last:border-b-0">
                      <td className="py-3 pr-4">{line.description || "—"}</td>
                      <td className="py-3 pr-4 text-right ltr-code">{line.quantity}</td>
                      <td className="py-3 pr-4 text-right ltr-code">
                        {Number(line.unit_price).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </td>
                      <td className="py-3 text-right font-medium ltr-code">
                        {Number(line.line_total ?? line.gross_amount ?? line.amount ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No line items.</p>
          )}
        </CardContent>
      </Card>

      {creditNote.posted_journal_entry_id && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>Posted Journal Entry</CardTitle>
          </CardHeader>
          <CardContent>
            <Link
              href={`/accounting/journal-entries/${creditNote.posted_journal_entry_id}`}
              className="flex items-center font-mono text-sm hover:text-primary hover:underline ltr-code"
            >
              <FileText className="ltr:mr-2 rtl:ml-2 h-4 w-4" />
              JE #{creditNote.posted_journal_entry_id}
            </Link>
          </CardContent>
        </Card>
      )}

      {(creditNote.notes || creditNote.reason_notes || creditNote.reference) && (
        <Card>
          <CardHeader>
            <CardTitle>Notes</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {creditNote.reason_notes && (
              <div>
                <span className="text-muted-foreground">Reason notes:</span>{" "}
                <span>{creditNote.reason_notes}</span>
              </div>
            )}
            {creditNote.reference && (
              <div>
                <span className="text-muted-foreground">Reference:</span>{" "}
                <span className="ltr-code">{creditNote.reference}</span>
              </div>
            )}
            {creditNote.notes && (
              <div>
                <span className="text-muted-foreground">Notes:</span>{" "}
                <span>{creditNote.notes}</span>
              </div>
            )}
          </CardContent>
        </Card>
      )}
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
