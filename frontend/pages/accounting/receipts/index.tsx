import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState, useMemo } from "react";
import { Plus, Search, FileText } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { customerReceiptsService, type CustomerReceiptListItem } from "@/services/accounts.service";

export default function CustomerReceiptsPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const [search, setSearch] = useState("");

  const { data: receipts, isLoading } = useQuery({
    queryKey: ["customer-receipts"],
    queryFn: async () => {
      const { data } = await customerReceiptsService.list();
      return data;
    },
  });

  const filtered = useMemo(() => {
    if (!receipts) return [];
    if (!search.trim()) return receipts;
    const q = search.toLowerCase();
    return receipts.filter(
      (r) =>
        r.customer_code.toLowerCase().includes(q) ||
        r.reference.toLowerCase().includes(q) ||
        r.memo.toLowerCase().includes(q) ||
        r.amount.includes(q)
    );
  }, [receipts, search]);

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

        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-4 mb-4">
              <div className="relative flex-1 max-w-sm">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder={t("common:search", "Search...")}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-9"
                />
              </div>
            </div>

            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner />
              </div>
            ) : !filtered.length ? (
              <EmptyState
                icon={<FileText className="h-12 w-12" />}
                title={t("accounting:noReceipts", "No receipts found")}
                description={
                  receipts?.length
                    ? t("accounting:noReceiptsSearch", "No receipts match your search criteria.")
                    : t("accounting:noReceiptsYet", "No customer receipts have been recorded yet.")
                }
                action={
                  !receipts?.length ? (
                    <Link href="/accounting/receipts/new">
                      <Button>
                        <Plus className="h-4 w-4 me-2" />
                        {t("accounting:newReceipt", "New Receipt")}
                      </Button>
                    </Link>
                  ) : undefined
                }
              />
            ) : (
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("accounting:date", "Date")}</TableHead>
                      <TableHead>{t("accounting:customer", "Customer")}</TableHead>
                      <TableHead>{t("accounting:reference", "Reference")}</TableHead>
                      <TableHead>{t("accounting:bankAccount", "Bank Account")}</TableHead>
                      <TableHead>{t("accounting:memo", "Memo")}</TableHead>
                      <TableHead className="text-right">{t("accounting:amount", "Amount")}</TableHead>
                      <TableHead>{"Journal Entry"}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filtered.map((receipt) => (
                      <TableRow key={receipt.receipt_public_id}>
                        <TableCell className="whitespace-nowrap">
                          {receipt.receipt_date}
                        </TableCell>
                        <TableCell className="font-medium">
                          {receipt.customer_code}
                        </TableCell>
                        <TableCell>{receipt.reference || "—"}</TableCell>
                        <TableCell>{receipt.bank_account_code}</TableCell>
                        <TableCell className="max-w-[200px] truncate">
                          {receipt.memo || "—"}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {receipt.currency && <span className="text-muted-foreground text-xs me-1">{receipt.currency}</span>}
                          {Number(receipt.amount).toLocaleString(undefined, {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}
                        </TableCell>
                        <TableCell>
                          {receipt.journal_entry_id ? (
                            <Link
                              href={`/accounting/journal-entries/${receipt.journal_entry_id}`}
                              className="text-primary hover:underline text-sm"
                            >
                              {receipt.journal_entry_number || "View JE"}
                            </Link>
                          ) : receipt.journal_entry_public_id ? (
                            <span className="text-destructive text-sm">
                              JE missing
                            </span>
                          ) : (
                            <span className="text-muted-foreground text-sm">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
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
