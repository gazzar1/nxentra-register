import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import Link from "next/link";
import { useRouter } from "next/router";
import { useState } from "react";
import { Plus, Search } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState } from "@/components/common";
import { PaginatedTable } from "@/components/common/PaginatedTable";
import type { ColumnDef } from "@/components/common/PaginatedTable";
import { useInventoryTransfers } from "@/queries/useInventory";
import {
  TRANSFER_STATUS_COLORS,
  TRANSFER_STATUS_LABELS,
  type InventoryTransferListItem,
} from "@/types/inventory";
import { cn } from "@/lib/cn";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

export default function InventoryTransfersPage() {
  const router = useRouter();
  const { formatDate } = useCompanyFormat();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("-transfer_date");

  const { data: response, isLoading } = useInventoryTransfers({
    page,
    page_size: pageSize,
    ordering,
  });

  const transfers = response?.results || [];
  const totalCount = response?.count || 0;
  const totalPages = response?.total_pages || 1;

  const columns: ColumnDef<InventoryTransferListItem>[] = [
    {
      key: "transfer_number",
      label: "Transfer #",
      sortable: true,
      render: (t) => (
        <Link
          href={`/inventory/transfers/${t.id}`}
          className="font-mono text-sm font-medium hover:text-primary hover:underline ltr-code"
        >
          {t.transfer_number}
        </Link>
      ),
    },
    {
      key: "transfer_date",
      label: "Date",
      sortable: true,
      render: (t) => <span className="text-sm text-muted-foreground">{formatDate(t.transfer_date)}</span>,
    },
    {
      key: "source_warehouse_code",
      label: "From",
      render: (t) => <span className="font-mono text-sm">{t.source_warehouse_code}</span>,
    },
    {
      key: "destination_warehouse_code",
      label: "To",
      render: (t) => <span className="font-mono text-sm">{t.destination_warehouse_code}</span>,
    },
    {
      key: "line_count",
      label: "Lines",
      render: (t) => <span className="text-sm text-muted-foreground">{t.line_count}</span>,
    },
    {
      key: "status",
      label: "Status",
      sortable: true,
      render: (t) => (
        <Badge className={cn("text-xs", TRANSFER_STATUS_COLORS[t.status])}>
          {TRANSFER_STATUS_LABELS[t.status]}
        </Badge>
      ),
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Inventory Transfers"
          subtitle="Move stock between warehouses"
          actions={
            <Link href="/inventory/transfers/new">
              <Button>
                <Plus className="h-4 w-4 me-2" />New Transfer
              </Button>
            </Link>
          }
        />
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-4 mb-6">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search transfers..."
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setPage(1);
                  }}
                  className="ps-10"
                />
              </div>
            </div>
            <PaginatedTable
              data={transfers}
              columns={columns}
              keyExtractor={(t) => t.id}
              page={page}
              pageSize={pageSize}
              totalCount={totalCount}
              totalPages={totalPages}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              ordering={ordering}
              onOrderingChange={setOrdering}
              onRowClick={(t) => router.push(`/inventory/transfers/${t.id}`)}
              isLoading={isLoading}
              emptyState={
                <EmptyState
                  title="No transfers yet"
                  description="Move stock from one warehouse to another by creating a transfer."
                />
              }
            />
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return { props: { ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])) } };
};
