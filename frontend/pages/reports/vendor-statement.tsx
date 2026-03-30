import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useQuery } from "@tanstack/react-query";
import { Printer, Search, Building2, Mail, Phone, MapPin, Landmark, Download } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Separator } from "@/components/ui/separator";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useBilingualText } from "@/components/common/BilingualText";
import { useVendors } from "@/queries/useAccounts";
import { reportsService } from "@/services/reports.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import { exportTransactionsCSV } from "@/lib/export";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

export default function VendorStatementPage() {
  const { t } = useTranslation(["common", "reports"]);
  const router = useRouter();
  const getText = useBilingualText();
  const { company } = useAuth();
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();

  const [selectedVendorCode, setSelectedVendorCode] = useState<string>("");
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");

  const { data: vendors } = useVendors();

  const { data: statement, isLoading, refetch } = useQuery({
    queryKey: ["vendor-statement", selectedVendorCode, dateFrom, dateTo],
    queryFn: async () => {
      if (!selectedVendorCode) return null;
      const params: { date_from?: string; date_to?: string } = {};
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const { data } = await reportsService.vendorStatement(selectedVendorCode, params);
      return data;
    },
    enabled: !!selectedVendorCode,
  });

  const handlePrint = () => {
    window.print();
  };

  const handleExportCSV = () => {
    if (!statement) return;
    exportTransactionsCSV(
      statement.transactions,
      statement.vendor.code,
      `vendor-statement-${statement.vendor.code}`
    );
  };

  const handleSearch = () => {
    refetch();
  };

  return (
    <AppLayout>
      <div className="space-y-6 print:space-y-0">
        <div className="no-print">
          <PageHeader
            title={t("reports:vendorStatement.title", "Vendor Statement")}
            subtitle={t("reports:vendorStatement.subtitle", "View account activity and balance for a vendor")}
            actions={
              <div className="flex gap-2">
                <Button variant="outline" onClick={handleExportCSV} disabled={!statement}>
                  <Download className="me-2 h-4 w-4" />
                  {t("reports:actions.exportCSV", "Export CSV")}
                </Button>
                <Button variant="outline" onClick={handlePrint} disabled={!statement}>
                  <Printer className="me-2 h-4 w-4" />
                  {t("reports:actions.print")}
                </Button>
              </div>
            }
          />
        </div>

        {/* Filter Card */}
        <Card className="no-print">
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-end gap-4">
              <div className="space-y-2 min-w-[200px]">
                <Label>{t("reports:filters.vendor", "Vendor")}</Label>
                <Select value={selectedVendorCode} onValueChange={setSelectedVendorCode}>
                  <SelectTrigger>
                    <SelectValue placeholder={t("reports:filters.selectVendor", "Select vendor")} />
                  </SelectTrigger>
                  <SelectContent>
                    {vendors?.map((vendor) => (
                      <SelectItem key={vendor.code} value={vendor.code}>
                        {vendor.code} - {vendor.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>{t("reports:filters.dateFrom", "Date From")}</Label>
                <Input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="w-40"
                />
              </div>

              <div className="space-y-2">
                <Label>{t("reports:filters.dateTo", "Date To")}</Label>
                <Input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="w-40"
                />
              </div>

              <Button onClick={handleSearch} disabled={!selectedVendorCode}>
                <Search className="me-2 h-4 w-4" />
                {t("reports:filters.search", "Search")}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Statement Content */}
        {isLoading ? (
          <Card>
            <CardContent className="py-12">
              <LoadingSpinner />
            </CardContent>
          </Card>
        ) : !statement ? (
          <Card>
            <CardContent className="py-12">
              <EmptyState
                title={t("reports:vendorStatement.noSelection", "Select a Vendor")}
                description={t("reports:vendorStatement.noSelectionDescription", "Choose a vendor to view their statement")}
              />
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Statement Header */}
            <Card>
              <CardHeader className="text-center print:pb-4">
                <CardTitle className="text-xl">{company?.name}</CardTitle>
                <p className="text-lg font-semibold">
                  {t("reports:vendorStatement.title", "Vendor Statement")}
                </p>
                <p className="text-sm text-muted-foreground">
                  {dateFrom || dateTo
                    ? `${dateFrom ? formatDate(dateFrom) : "..."} - ${dateTo ? formatDate(dateTo) : "..."}`
                    : t("reports:vendorStatement.allTime", "All Time")}
                </p>
              </CardHeader>
              <CardContent>
                {/* Vendor Info */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                  <div className="space-y-2">
                    <h3 className="font-semibold flex items-center gap-2">
                      <Building2 className="h-4 w-4" />
                      {t("reports:vendorStatement.vendorInfo", "Vendor Information")}
                    </h3>
                    <div className="text-sm space-y-1">
                      <p className="font-medium">{statement.vendor.name}</p>
                      <p className="text-muted-foreground">{statement.vendor.code}</p>
                      {statement.vendor.email && (
                        <p className="flex items-center gap-2">
                          <Mail className="h-3 w-3" /> {statement.vendor.email}
                        </p>
                      )}
                      {statement.vendor.phone && (
                        <p className="flex items-center gap-2">
                          <Phone className="h-3 w-3" /> {statement.vendor.phone}
                        </p>
                      )}
                      {statement.vendor.address && (
                        <p className="flex items-center gap-2">
                          <MapPin className="h-3 w-3" /> {statement.vendor.address}
                        </p>
                      )}
                      {statement.vendor.bank_name && (
                        <p className="flex items-center gap-2">
                          <Landmark className="h-3 w-3" /> {statement.vendor.bank_name} - {statement.vendor.bank_account}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Account Summary */}
                  <div className="space-y-2">
                    <h3 className="font-semibold">{t("reports:vendorStatement.accountSummary", "Account Summary")}</h3>
                    <div className="grid grid-cols-2 gap-2 text-sm">
                      <span className="text-muted-foreground">{t("reports:vendorStatement.currentBalance", "Current Balance")}:</span>
                      <span className={cn("font-medium ltr-number text-end", parseFloat(statement.balance.balance) > 0 && "text-amber-600")}>
                        {formatCurrency(statement.balance.balance)}
                      </span>

                      <span className="text-muted-foreground">{t("reports:vendorStatement.totalBills", "Total Bills")}:</span>
                      <span className="ltr-number text-end">{formatCurrency(statement.balance.credit_total)}</span>

                      <span className="text-muted-foreground">{t("reports:vendorStatement.totalPayments", "Total Payments")}:</span>
                      <span className="ltr-number text-end">{formatCurrency(statement.balance.debit_total)}</span>

                      <span className="text-muted-foreground">{t("reports:vendorStatement.paymentTerms", "Payment Terms")}:</span>
                      <span className="text-end">{statement.vendor.payment_terms_days} days</span>

                      <span className="text-muted-foreground">{t("reports:vendorStatement.lastBillDate", "Last Bill Date")}:</span>
                      <span className="text-end">
                        {statement.balance.last_bill_date ? formatDate(statement.balance.last_bill_date) : "-"}
                      </span>

                      <span className="text-muted-foreground">{t("reports:vendorStatement.lastPaymentDate", "Last Payment Date")}:</span>
                      <span className="text-end">
                        {statement.balance.last_payment_date ? formatDate(statement.balance.last_payment_date) : "-"}
                      </span>
                    </div>
                  </div>
                </div>

                <Separator />

                {/* Aging Summary */}
                <div className="my-6">
                  <h3 className="font-semibold mb-3">{t("reports:vendorStatement.agingSummary", "Aging Summary")}</h3>
                  <div className="grid grid-cols-5 gap-2 text-sm">
                    <div className="text-center p-3 bg-muted rounded">
                      <p className="text-muted-foreground">{t("reports:aging.current", "Current")}</p>
                      <p className="font-medium ltr-number">{formatCurrency(statement.aging.current)}</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded">
                      <p className="text-muted-foreground">{t("reports:aging.31_60", "31-60 Days")}</p>
                      <p className="font-medium ltr-number">{formatCurrency(statement.aging.days_31_60)}</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded">
                      <p className="text-muted-foreground">{t("reports:aging.61_90", "61-90 Days")}</p>
                      <p className="font-medium ltr-number">{formatCurrency(statement.aging.days_61_90)}</p>
                    </div>
                    <div className="text-center p-3 bg-muted rounded">
                      <p className="text-muted-foreground">{t("reports:aging.over_90", "Over 90 Days")}</p>
                      <p className={cn("font-medium ltr-number", parseFloat(statement.aging.over_90) > 0 && "text-red-600")}>
                        {formatCurrency(statement.aging.over_90)}
                      </p>
                    </div>
                    <div className="text-center p-3 bg-primary/10 rounded">
                      <p className="text-muted-foreground">{t("reports:aging.total", "Total")}</p>
                      <p className="font-bold ltr-number">{formatCurrency(statement.aging.total)}</p>
                    </div>
                  </div>
                </div>

                <Separator />

                {/* Payment Allocations */}
                {statement.payment_allocations.length > 0 && (
                  <div className="my-6">
                    <h3 className="font-semibold mb-3">{t("reports:vendorStatement.paymentAllocations", "Recent Payment Allocations")}</h3>
                    <div className="rounded-md border">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>{t("reports:columns.paymentDate", "Payment Date")}</TableHead>
                            <TableHead>{t("reports:columns.billReference", "Bill Reference")}</TableHead>
                            <TableHead>{t("reports:columns.billDate", "Bill Date")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.billAmount", "Bill Amount")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.amountPaid", "Amount Paid")}</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {statement.payment_allocations.map((alloc, idx) => (
                            <TableRow key={idx}>
                              <TableCell>{formatDate(alloc.payment_date)}</TableCell>
                              <TableCell className="font-medium">{alloc.bill_reference}</TableCell>
                              <TableCell>{alloc.bill_date ? formatDate(alloc.bill_date) : "-"}</TableCell>
                              <TableCell className="text-right ltr-number">
                                {alloc.bill_amount ? formatCurrency(alloc.bill_amount) : "-"}
                              </TableCell>
                              <TableCell className="text-right ltr-number font-medium">{formatCurrency(alloc.amount_paid)}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                )}

                <Separator />

                {/* Transaction History */}
                <div className="mt-6">
                  <h3 className="font-semibold mb-3">{t("reports:vendorStatement.transactions", "Transaction History")}</h3>
                  {statement.transactions.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-4">
                      {t("reports:vendorStatement.noTransactions", "No transactions found for this period.")}
                    </p>
                  ) : (
                    <div className="rounded-md border">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>{t("reports:columns.date", "Date")}</TableHead>
                            <TableHead>{t("reports:columns.entryNumber", "Entry #")}</TableHead>
                            <TableHead>{t("reports:columns.description", "Description")}</TableHead>
                            <TableHead>{t("reports:columns.reference", "Reference")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.debit", "Debit")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.credit", "Credit")}</TableHead>
                            <TableHead className="text-right">{t("reports:columns.balance", "Balance")}</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {statement.transactions.map((txn, idx) => (
                            <TableRow key={idx}>
                              <TableCell>{formatDate(txn.date)}</TableCell>
                              <TableCell className="font-mono text-xs">{txn.entry_number}</TableCell>
                              <TableCell>{txn.description}</TableCell>
                              <TableCell>{txn.reference || "-"}</TableCell>
                              <TableCell className="text-right ltr-number">
                                {parseFloat(txn.debit) > 0 ? formatCurrency(txn.debit) : "-"}
                              </TableCell>
                              <TableCell className="text-right ltr-number">
                                {parseFloat(txn.credit) > 0 ? formatCurrency(txn.credit) : "-"}
                              </TableCell>
                              <TableCell className="text-right ltr-number font-medium">{formatCurrency(txn.balance)}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "reports"])),
    },
  };
};
