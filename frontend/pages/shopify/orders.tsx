import { useState, useEffect, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import {
  Package,
  Search,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { shopifyService, ShopifyOrder } from "@/services/shopify.service";

function statusBadge(status: ShopifyOrder["status"]) {
  switch (status) {
    case "PROCESSED":
      return (
        <Badge variant="default" className="bg-green-100 text-green-800 hover:bg-green-100">
          <CheckCircle2 className="me-1 h-3 w-3" />
          Processed
        </Badge>
      );
    case "PENDING_CAPTURE":
      return (
        <Badge variant="default" className="bg-amber-100 text-amber-800 hover:bg-amber-100">
          <Clock className="me-1 h-3 w-3" />
          Pending Capture
        </Badge>
      );
    case "CANCELLED":
      return (
        <Badge variant="default" className="bg-gray-200 text-gray-700 hover:bg-gray-200">
          <XCircle className="me-1 h-3 w-3" />
          Cancelled
        </Badge>
      );
    case "ERROR":
      return (
        <Badge variant="destructive">
          <XCircle className="me-1 h-3 w-3" />
          Error
        </Badge>
      );
    default:
      return (
        <Badge variant="secondary">
          <Clock className="me-1 h-3 w-3" />
          Received
        </Badge>
      );
  }
}

export default function ShopifyOrdersPage() {
  const { t } = useTranslation(["common"]);
  const { toast } = useToast();

  const [orders, setOrders] = useState<ShopifyOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const fetchOrders = async () => {
    setLoading(true);
    try {
      const { data } = await shopifyService.getOrders();
      setOrders(data);
    } catch {
      toast({ title: "Failed to load orders.", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOrders();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(() => {
    if (!search.trim()) return orders;
    const q = search.toLowerCase();
    return orders.filter(
      (o) =>
        o.shopify_order_name.toLowerCase().includes(q) ||
        o.shopify_order_number.toLowerCase().includes(q) ||
        o.financial_status.toLowerCase().includes(q) ||
        o.gateway.toLowerCase().includes(q)
    );
  }, [orders, search]);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Shopify Orders"
          subtitle="Orders received from Shopify webhooks"
          actions={
            <Button variant="outline" size="sm" onClick={fetchOrders} disabled={loading}>
              <RefreshCw className={`me-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          }
        />

        <Card>
          <CardContent className="pt-6">
            <div className="mb-4">
              <div className="relative max-w-sm">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search orders..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-9"
                />
              </div>
            </div>

            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : filtered.length === 0 ? (
              <EmptyState
                icon={<Package className="h-12 w-12" />}
                title={search ? "No matching orders" : "No orders yet"}
                description={
                  search
                    ? "Try a different search term."
                    : "Orders will appear here when Shopify sends payment webhooks."
                }
              />
            ) : (
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Order</TableHead>
                      <TableHead>Date</TableHead>
                      <TableHead className="text-right">Total</TableHead>
                      <TableHead className="text-right">Tax</TableHead>
                      <TableHead>Payment</TableHead>
                      <TableHead>Financial Status</TableHead>
                      <TableHead>Sync Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filtered.map((order) => (
                      <TableRow key={order.id}>
                        <TableCell className="font-medium font-mono">
                          {order.shopify_order_name || `#${order.shopify_order_number}`}
                        </TableCell>
                        <TableCell>
                          {new Date(order.order_date).toLocaleDateString()}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {order.currency} {Number(order.total_price).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {Number(order.total_tax) > 0
                            ? `${order.currency} ${Number(order.total_tax).toLocaleString(undefined, { minimumFractionDigits: 2 })}`
                            : "—"}
                        </TableCell>
                        <TableCell>{order.gateway || "—"}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className="capitalize">
                            {order.financial_status.replace("_", " ")}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {statusBadge(order.status)}
                          {order.status === "ERROR" && order.error_message && (
                            <p className="mt-1 text-xs text-destructive">{order.error_message}</p>
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
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
