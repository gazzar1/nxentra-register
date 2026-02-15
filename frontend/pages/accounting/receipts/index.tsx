import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { Plus, FileText, ExternalLink } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { PageHeader } from "@/components/common";

export default function CustomerReceiptsPage() {
  const { t } = useTranslation(["common", "accounting"]);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("accounting:customerReceipts", "Customer Receipts")}
          subtitle={t("accounting:customerReceiptsSubtitle", "Record payments received from customers")}
          actions={
            <Link href="/accounting/receipts/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />
                {t("accounting:newReceipt", "New Receipt")}
              </Button>
            </Link>
          }
        />

        <div className="grid gap-6 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Plus className="h-5 w-5 text-emerald-500" />
                {t("accounting:recordReceipt", "Record a Receipt")}
              </CardTitle>
              <CardDescription>
                {t("accounting:recordReceiptDescription", "Record a payment received from a customer. This will create a journal entry that debits your bank account and credits accounts receivable.")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Link href="/accounting/receipts/new">
                <Button className="w-full">
                  <Plus className="h-4 w-4 me-2" />
                  {t("accounting:newReceipt", "New Receipt")}
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
                {t("accounting:viewReceiptsInJournal", "Customer receipts are recorded as journal entries. View the journal to see all posted receipts and their GL impact.")}
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
            <CardTitle>{t("accounting:howReceiptsWork", "How Receipts Work")}</CardTitle>
          </CardHeader>
          <CardContent className="prose prose-sm dark:prose-invert max-w-none">
            <p>
              {t("accounting:receiptsExplanation", "When you record a customer receipt, the system automatically creates a journal entry with the following postings:")}
            </p>
            <ul>
              <li>
                <strong>{t("accounting:debit", "Debit")}:</strong> {t("accounting:bankAccountDebited", "Bank Account (increases cash on hand)")}
              </li>
              <li>
                <strong>{t("accounting:credit", "Credit")}:</strong> {t("accounting:arCredited", "AR Control Account (reduces the amount owed by the customer)")}
              </li>
            </ul>
            <p>
              {t("accounting:receiptPosting", "The journal entry is automatically posted, updating your account balances and reducing the customer's outstanding balance.")}
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
