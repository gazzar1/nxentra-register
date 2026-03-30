// pages/inventory/ledger/index.tsx
// Stock ledger page - shows all stock movements

import { useState } from "react";
import { useTranslation } from "next-i18next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { GetServerSideProps } from "next";
import { Search, FileText, ArrowUpRight, ArrowDownRight } from "lucide-react";
import { format } from "date-fns";

import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useStockLedger, useWarehouses } from "@/queries/useInventory";
import { StockLedgerFilters, StockLedgerSourceType } from "@/types/inventory";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

const sourceTypes: StockLedgerSourceType[] = [
  "PURCHASE_BILL",
  "SALES_INVOICE",
  "ADJUSTMENT",
  "OPENING_BALANCE",
  "TRANSFER_IN",
  "TRANSFER_OUT",
  "SALES_RETURN",
  "PURCHASE_RETURN",
];

const sourceTypeColors: Record<StockLedgerSourceType, string> = {
  PURCHASE_BILL: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
  SALES_INVOICE: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300",
  ADJUSTMENT: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300",
  OPENING_BALANCE: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300",
  TRANSFER_IN: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900 dark:text-cyan-300",
  TRANSFER_OUT: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-300",
  SALES_RETURN: "bg-pink-100 text-pink-800 dark:bg-pink-900 dark:text-pink-300",
  PURCHASE_RETURN: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
};

export default function StockLedgerPage() {
  const { t } = useTranslation(["common", "inventory"]);
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();
  const [filters, setFilters] = useState<StockLedgerFilters>({});

  const { data: entries, isLoading } = useStockLedger(filters);
  const { data: warehouses } = useWarehouses({ is_active: true });

  const handleFilterChange = (key: keyof StockLedgerFilters, value: string) => {
    setFilters((prev) => ({
      ...prev,
      [key]: value || undefined,
    }));
  };

  const formatQty = (value: string) => {
    const num = parseFloat(value);
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 4,
      signDisplay: "always",
    }).format(num);
  };

  return (
    <AppLayout>
      <PageHeader
        title={t("inventory:ledger.title")}
        subtitle={t("inventory:ledger.subtitle")}
      />

      {/* Filters */}
      <Card className="mb-6">
        <CardContent className="pt-6">
          <div className="flex flex-col md:flex-row gap-4">
            <div className="flex-1">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder={t("inventory:ledger.searchByItem")}
                  className="pl-10"
                  value={filters.item_code || ""}
                  onChange={(e) => handleFilterChange("item_code", e.target.value)}
                />
              </div>
            </div>
            <Select
              value={filters.warehouse_code || "all"}
              onValueChange={(value) =>
                handleFilterChange("warehouse_code", value === "all" ? "" : value)
              }
            >
              <SelectTrigger className="w-full md:w-[200px]">
                <SelectValue placeholder={t("inventory:ledger.allWarehouses")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("inventory:ledger.allWarehouses")}</SelectItem>
                {warehouses?.map((warehouse) => (
                  <SelectItem key={warehouse.id} value={warehouse.code}>
                    {warehouse.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={filters.source_type || "all"}
              onValueChange={(value) =>
                handleFilterChange("source_type", value === "all" ? "" : value)
              }
            >
              <SelectTrigger className="w-full md:w-[180px]">
                <SelectValue placeholder={t("inventory:ledger.allTypes")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("inventory:ledger.allTypes")}</SelectItem>
                {sourceTypes.map((type) => (
                  <SelectItem key={type} value={type}>
                    {t(`inventory:ledger.sourceTypes.${type}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* Ledger Table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-6 space-y-4">
              {[...Array(8)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : entries && entries.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[60px]">#</TableHead>
                  <TableHead>{t("inventory:ledger.date")}</TableHead>
                  <TableHead>{t("inventory:ledger.source")}</TableHead>
                  <TableHead>{t("inventory:ledger.item")}</TableHead>
                  <TableHead>{t("inventory:ledger.warehouse")}</TableHead>
                  <TableHead className="text-right">{t("inventory:ledger.qtyDelta")}</TableHead>
                  <TableHead className="text-right">{t("inventory:ledger.unitCost")}</TableHead>
                  <TableHead className="text-right">{t("inventory:ledger.valueDelta")}</TableHead>
                  <TableHead className="text-right">{t("inventory:ledger.qtyAfter")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entries.map((entry) => {
                  const isPositive = parseFloat(entry.qty_delta) > 0;
                  return (
                    <TableRow key={entry.id}>
                      <TableCell className="font-mono text-muted-foreground">
                        {entry.sequence}
                      </TableCell>
                      <TableCell>
                        {format(new Date(entry.posted_at), "MMM d, yyyy HH:mm")}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={sourceTypeColors[entry.source_type]}
                        >
                          {t(`inventory:ledger.sourceTypes.${entry.source_type}`)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col">
                          <span className="font-medium">{entry.item_code}</span>
                          <span className="text-sm text-muted-foreground">
                            {entry.item_name}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{entry.warehouse_code}</Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <span
                          className={`inline-flex items-center gap-1 ${
                            isPositive ? "text-green-600" : "text-red-600"
                          }`}
                        >
                          {isPositive ? (
                            <ArrowUpRight className="h-4 w-4" />
                          ) : (
                            <ArrowDownRight className="h-4 w-4" />
                          )}
                          {formatQty(entry.qty_delta)}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        {formatAmount(entry.unit_cost)}
                      </TableCell>
                      <TableCell className="text-right">
                        <span
                          className={
                            parseFloat(entry.value_delta) >= 0
                              ? "text-green-600"
                              : "text-red-600"
                          }
                        >
                          {formatAmount(entry.value_delta)}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-medium">
                        {formatQty(entry.qty_balance_after).replace("+", "")}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <FileText className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-medium">{t("inventory:ledger.noEntries")}</h3>
              <p className="text-muted-foreground mt-1">
                {t("inventory:ledger.noEntriesDescription")}
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "inventory"])),
    },
  };
};
