// pages/inventory/warehouses/index.tsx
// Warehouse list page

import { useState } from "react";
import { useRouter } from "next/router";
import { useTranslation } from "next-i18next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { GetServerSideProps } from "next";
import { Plus, Search, Building2 } from "lucide-react";

import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
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
import { Badge } from "@/components/ui/badge";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { EmptyState } from "@/components/common/EmptyState";

import { useWarehouses } from "@/queries/useInventory";

export default function WarehousesPage() {
  const { t } = useTranslation(["common", "inventory"]);
  const router = useRouter();
  const [search, setSearch] = useState("");

  const { data: warehouses, isLoading } = useWarehouses();

  const filteredWarehouses = warehouses?.filter(
    (warehouse) =>
      warehouse.code.toLowerCase().includes(search.toLowerCase()) ||
      warehouse.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <AppLayout>
      <PageHeader
        title={t("inventory:warehouses.title")}
        subtitle={t("inventory:warehouses.subtitle")}
        actions={
          <Button onClick={() => router.push("/inventory/warehouses/new")}>
            <Plus className="h-4 w-4 me-2" />
            {t("inventory:warehouses.create")}
          </Button>
        }
      />

      <Card>
        <CardContent className="pt-6">
          {/* Search */}
          <div className="flex items-center gap-4 mb-6">
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute start-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder={t("common:search")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="ps-10"
              />
            </div>
          </div>

          {/* Table */}
          {isLoading ? (
            <div className="flex justify-center py-12">
              <LoadingSpinner />
            </div>
          ) : filteredWarehouses?.length === 0 ? (
            <EmptyState
              icon={<Building2 className="h-12 w-12" />}
              title={t("inventory:warehouses.noWarehouses")}
              description={t("inventory:warehouses.noWarehousesDescription")}
              action={
                <Button onClick={() => router.push("/inventory/warehouses/new")}>
                  <Plus className="h-4 w-4 me-2" />
                  {t("inventory:warehouses.create")}
                </Button>
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("common:code")}</TableHead>
                  <TableHead>{t("common:name")}</TableHead>
                  <TableHead>{t("common:address")}</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Default</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredWarehouses?.map((warehouse) => (
                  <TableRow
                    key={warehouse.id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => router.push(`/inventory/warehouses/${warehouse.id}/edit`)}
                  >
                    <TableCell className="font-mono">{warehouse.code}</TableCell>
                    <TableCell>
                      <div>
                        <div>{warehouse.name}</div>
                        {warehouse.name_ar && (
                          <div className="text-sm text-muted-foreground" dir="rtl">
                            {warehouse.name_ar}
                          </div>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-xs truncate">
                      {warehouse.address || "-"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={warehouse.is_active ? "default" : "secondary"}>
                        {warehouse.is_active ? t("common:status.active") : t("common:status.inactive")}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {warehouse.is_default && (
                        <Badge variant="outline">{t("inventory:warehouses.isDefault")}</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
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
