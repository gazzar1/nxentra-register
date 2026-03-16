import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import {
  ShoppingCart,
  Package,
  Settings,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Loader2,
  ArrowRight,
  ReceiptText,
  Undo2,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/common";
import {
  shopifyService,
  ShopifyStore,
  ShopifyOrder,
} from "@/services/shopify.service";

export default function ShopifyDashboardPage() {
  const { t } = useTranslation(["common"]);

  const [store, setStore] = useState<ShopifyStore | null>(null);
  const [orders, setOrders] = useState<ShopifyOrder[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [storeRes, ordersRes] = await Promise.allSettled([
          shopifyService.getStore(),
          shopifyService.getOrders(),
        ]);

        if (storeRes.status === "fulfilled") {
          const d = storeRes.value.data as any;
          if (!d.connected) {
            setStore(null);
          } else if (d.stores && d.stores.length > 0) {
            setStore(d.stores[0] as ShopifyStore);
          } else if (d.status) {
            // Direct store object (legacy format)
            setStore(d as ShopifyStore);
          } else {
            setStore(null);
          }
        }

        if (ordersRes.status === "fulfilled") {
          setOrders(ordersRes.value.data);
        }
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const isConnected = store?.status === "ACTIVE";

  const stats = {
    total: orders.length,
    processed: orders.filter((o) => o.status === "PROCESSED").length,
    errors: orders.filter((o) => o.status === "ERROR").length,
    revenue: orders
      .filter((o) => o.status === "PROCESSED")
      .reduce((sum, o) => sum + Number(o.total_price), 0),
    currency: orders[0]?.currency ?? "USD",
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Shopify"
          subtitle="Ecommerce integration overview"
        />

        {loading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : !isConnected ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-12 text-center">
              <ShoppingCart className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">No Shopify Store Connected</h3>
              <p className="text-sm text-muted-foreground mb-6 max-w-md">
                Connect your Shopify store to automatically sync orders, track revenue,
                and create journal entries from your ecommerce sales.
              </p>
              <Link href="/shopify/settings">
                <Button>
                  <Settings className="me-2 h-4 w-4" />
                  Go to Settings
                </Button>
              </Link>
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Connection Status */}
            <Card>
              <CardContent className="pt-6">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-green-100">
                      <CheckCircle2 className="h-5 w-5 text-green-600" />
                    </div>
                    <div>
                      <p className="font-semibold">Connected to {store.shop_domain}</p>
                      <p className="text-sm text-muted-foreground">
                        {store.webhooks_registered
                          ? "Webhooks active — orders sync automatically"
                          : "Webhooks not registered — go to Settings to register"}
                      </p>
                    </div>
                  </div>
                  <Link href="/shopify/settings">
                    <Button variant="outline" size="sm">
                      <Settings className="me-2 h-4 w-4" />
                      Settings
                    </Button>
                  </Link>
                </div>
              </CardContent>
            </Card>

            {/* Stats Grid */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total Orders</CardTitle>
                  <Package className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.total}</div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Processed</CardTitle>
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.processed}</div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Errors</CardTitle>
                  <AlertCircle className="h-4 w-4 text-destructive" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.errors}</div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Revenue (Processed)</CardTitle>
                  <ReceiptText className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">
                    {stats.currency}{" "}
                    {stats.revenue.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Recent Orders */}
            <Card>
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle>Recent Orders</CardTitle>
                <Link href="/shopify/orders">
                  <Button variant="ghost" size="sm">
                    View All
                    <ArrowRight className="ms-2 h-4 w-4" />
                  </Button>
                </Link>
              </CardHeader>
              <CardContent>
                {orders.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-6 text-center">
                    No orders received yet. Orders will appear here when customers pay.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {orders.slice(0, 5).map((order) => (
                      <div
                        key={order.id}
                        className="flex items-center justify-between rounded-lg border p-3"
                      >
                        <div className="flex items-center gap-3">
                          <div className="flex h-8 w-8 items-center justify-center rounded bg-muted">
                            <Package className="h-4 w-4 text-muted-foreground" />
                          </div>
                          <div>
                            <p className="text-sm font-medium font-mono">
                              {order.shopify_order_name || `#${order.shopify_order_number}`}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {new Date(order.order_date).toLocaleDateString()}
                              {order.gateway ? ` · ${order.gateway}` : ""}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-4">
                          <span className="text-sm font-mono font-medium">
                            {order.currency} {Number(order.total_price).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                          </span>
                          {order.status === "PROCESSED" ? (
                            <CheckCircle2 className="h-4 w-4 text-green-500" />
                          ) : order.status === "ERROR" ? (
                            <XCircle className="h-4 w-4 text-destructive" />
                          ) : (
                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
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
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
