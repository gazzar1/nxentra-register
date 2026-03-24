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
import { vendorPaymentsService, type VendorPaymentListItem } from "@/services/accounts.service";

export default function VendorPaymentsPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const [search, setSearch] = useState("");

  const { data: payments, isLoading } = useQuery({
    queryKey: ["vendor-payments"],
    queryFn: async () => {
      const { data } = await vendorPaymentsService.list();
      return data;
    },
  });

  const filtered = useMemo(() => {
    if (!payments) return [];
    if (!search.trim()) return payments;
    const q = search.toLowerCase();
    return payments.filter(
      (p) =>
        p.vendor_code.toLowerCase().includes(q) ||
        p.reference.toLowerCase().includes(q) ||
        p.memo.toLowerCase().includes(q) ||
        p.amount.includes(q)
    );
  }, [payments, search]);

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
                title={t("accounting:noPayments", "No payments found")}
                description={
                  payments?.length
                    ? t("accounting:noPaymentsSearch", "No payments match your search criteria.")
                    : t("accounting:noPaymentsYet", "No vendor payments have been recorded yet.")
                }
                action={
                  !payments?.length ? (
                    <Link href="/accounting/payments/new">
                      <Button>
                        <Plus className="h-4 w-4 me-2" />
                        {t("accounting:newPayment", "New Payment")}
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
                      <TableHead>{t("accounting:vendor", "Vendor")}</TableHead>
                      <TableHead>{t("accounting:reference", "Reference")}</TableHead>
                      <TableHead>{t("accounting:bankAccount", "Bank Account")}</TableHead>
                      <TableHead>{t("accounting:memo", "Memo")}</TableHead>
                      <TableHead className="text-right">{t("accounting:amount", "Amount")}</TableHead>
                      <TableHead>{t("accounting:journalEntry", "Journal Entry")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filtered.map((payment) => (
                      <TableRow key={payment.payment_public_id}>
                        <TableCell className="whitespace-nowrap">
                          {payment.payment_date}
                        </TableCell>
                        <TableCell className="font-medium">
                          {payment.vendor_code}
                        </TableCell>
                        <TableCell>{payment.reference || "—"}</TableCell>
                        <TableCell>{payment.bank_account_code}</TableCell>
                        <TableCell className="max-w-[200px] truncate">
                          {payment.memo || "—"}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {Number(payment.amount).toLocaleString(undefined, {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}
                        </TableCell>
                        <TableCell>
                          {payment.journal_entry_public_id ? (
                            <Link
                              href={`/accounting/journal-entries`}
                              className="text-primary hover:underline text-sm"
                            >
                              {t("accounting:viewJE", "View JE")}
                            </Link>
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
