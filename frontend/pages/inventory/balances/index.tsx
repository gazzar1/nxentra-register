// pages/inventory/balances/index.tsx
// Inventory balances list page

import { useState } from "react";
import { useTranslation } from "next-i18next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { GetServerSideProps } from "next";
import { Search, Package, AlertCircle } from "lucide-react";

import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { useInventoryBalances, useWarehouses, useInventorySummary } from "@/queries/useInventory";
import { InventoryBalanceFilters } from "@/types/inventory";

export default function InventoryBalancesPage() {
  const { t } = useTranslation(["common", "inventory"]);
  const [filters, setFilters] = useState<InventoryBalanceFilters>({});

  const { data: balances, isLoading } = useInventoryBalances(filters);
  const { data: warehouses } = useWarehouses({ is_active: true });
  const { data: summary } = useInventorySummary();

  const handleFilterChange = (key: keyof InventoryBalanceFilters, value: string) => {
    setFilters((prev) => ({
      ...prev,
      [key]: value || undefined,
    }));
  };

  const formatNumber = (value: string) => {
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(parseFloat(value));
  };

  const formatQty = (value: string) => {
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 4,
    }).format(parseFloat(value));
  };

  return (
    <AppLayout>
      <PageHeader
        title={t("inventory:balances.title")}
        subtitle={t("inventory:balances.subtitle")}
      />

      {/* Summary Cards */}
      {summary && (
        <div className="grid gap-4 md:grid-cols-3 mb-6">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                {t("inventory:balances.totalItems")}
              </CardTitle>
              <Package className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{summary.total_items}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                {t("inventory:balances.totalValue")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatNumber(summary.total_value)}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                {t("inventory:balances.warehouseCount")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{summary.warehouses?.length || 0}</div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Filters */}
      <Card className="mb-6">
        <CardContent className="pt-6">
          <div className="flex flex-col md:flex-row gap-4">
            <div className="flex-1">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder={t("inventory:balances.searchByItem")}
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
                <SelectValue placeholder={t("inventory:balances.allWarehouses")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("inventory:balances.allWarehouses")}</SelectItem>
                {warehouses?.map((warehouse) => (
                  <SelectItem key={warehouse.id} value={warehouse.code}>
                    {warehouse.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={filters.has_stock === undefined ? "all" : filters.has_stock ? "true" : "false"}
              onValueChange={(value) =>
                handleFilterChange(
                  "has_stock",
                  value === "all" ? "" : value
                )
              }
            >
              <SelectTrigger className="w-full md:w-[150px]">
                <SelectValue placeholder={t("inventory:balances.stockStatus")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("inventory:balances.allStock")}</SelectItem>
                <SelectItem value="true">{t("inventory:balances.inStock")}</SelectItem>
                <SelectItem value="false">{t("inventory:balances.outOfStock")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* Balances Table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-6 space-y-4">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : balances && balances.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("inventory:balances.itemCode")}</TableHead>
                  <TableHead>{t("inventory:balances.itemName")}</TableHead>
                  <TableHead>{t("inventory:balances.warehouse")}</TableHead>
                  <TableHead className="text-right">{t("inventory:balances.qtyOnHand")}</TableHead>
                  <TableHead className="text-right">{t("inventory:balances.avgCost")}</TableHead>
                  <TableHead className="text-right">{t("inventory:balances.stockValue")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {balances.map((balance) => (
                  <TableRow key={balance.id}>
                    <TableCell className="font-medium">{balance.item_code}</TableCell>
                    <TableCell>{balance.item_name}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{balance.warehouse_code}</Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <span
                        className={
                          parseFloat(balance.qty_on_hand) <= 0
                            ? "text-destructive"
                            : ""
                        }
                      >
                        {formatQty(balance.qty_on_hand)}
                      </span>
                    </TableCell>
                    <TableCell className="text-right">{formatNumber(balance.avg_cost)}</TableCell>
                    <TableCell className="text-right font-medium">
                      {formatNumber(balance.stock_value)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <AlertCircle className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-medium">{t("inventory:balances.noBalances")}</h3>
              <p className="text-muted-foreground mt-1">
                {t("inventory:balances.noBalancesDescription")}
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
