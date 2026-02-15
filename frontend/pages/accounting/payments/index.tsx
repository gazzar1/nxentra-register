import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { Plus, FileText, ExternalLink } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { PageHeader } from "@/components/common";

export default function VendorPaymentsPage() {
  const { t } = useTranslation(["common", "accounting"]);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("accounting:vendorPayments", "Vendor Payments")}
          subtitle={t("accounting:vendorPaymentsSubtitle", "Record payments made to vendors")}
          actions={
            <Link href="/accounting/payments/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />
                {t("accounting:newPayment", "New Payment")}
              </Button>
            </Link>
          }
        />

        <div className="grid gap-6 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Plus className="h-5 w-5 text-violet-500" />
                {t("accounting:recordPayment", "Record a Payment")}
              </CardTitle>
              <CardDescription>
                {t("accounting:recordPaymentDescription", "Record a payment made to a vendor. This will create a journal entry that debits accounts payable and credits your bank account.")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Link href="/accounting/payments/new">
                <Button className="w-full">
                  <Plus className="h-4 w-4 me-2" />
                  {t("accounting:newPayment", "New Payment")}
                </Button>
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileText className="h-5 w-5 text-blue-500" />
                {t("accounting:viewJournalEntries", "View Journal Entries")}
              </CardTitle>
              <CardDescription>
                {t("accounting:viewPaymentsInJournal", "Vendor payments are recorded as journal entries. View the journal to see all posted payments and their GL impact.")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Link href="/accounting/journal-entries">
                <Button variant="outline" className="w-full">
                  <ExternalLink className="h-4 w-4 me-2" />
                  {t("accounting:goToJournalEntries", "Go to Journal Entries")}
                </Button>
              </Link>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>{t("accounting:howPaymentsWork", "How Payments Work")}</CardTitle>
          </CardHeader>
          <CardContent className="prose prose-sm dark:prose-invert max-w-none">
            <p>
              {t("accounting:paymentsExplanation", "When you record a vendor payment, the system automatically creates a journal entry with the following postings:")}
            </p>
            <ul>
              <li>
                <strong>{t("accounting:debit", "Debit")}:</strong> {t("accounting:apDebited", "AP Control Account (reduces the amount you owe)")}
              </li>
              <li>
                <strong>{t("accounting:credit", "Credit")}:</strong> {t("accounting:bankCredited", "Bank Account (reduces cash on hand)")}
              </li>
            </ul>
            <p>
              {t("accounting:paymentPosting", "The journal entry is automatically posted, updating your account balances and reducing the vendor's outstanding balance.")}
            </p>
          </CardContent>
        </Card>
      </div>
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
