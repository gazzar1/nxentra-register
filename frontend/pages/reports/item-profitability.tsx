import { useState, useEffect } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import {
  reportsService,
  ItemProfitabilityResponse,
  ItemProfitabilityRow,
} from "@/services/reports.service";
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  Package,
  ShoppingCart,
  Percent,
} from "lucide-react";

export default function ItemProfitabilityPage() {
  const { t } = useTranslation("common");
  const { formatAmount } = useCompanyFormat();

  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<ItemProfitabilityResponse | null>(null);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const loadData = async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const res = await reportsService.itemProfitability(params);
      setData(res.data);
    } catch {
      // Error handled
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const summary = data?.summary;
  const items = data?.items || [];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Item Profitability"
          subtitle="Revenue, cost, and margin analysis per product"
        />

        {/* Date filter */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <Label>From</Label>
                <Input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="w-40"
                />
              </div>
              <div>
                <Label>To</Label>
                <Input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="w-40"
                />
              </div>
              <Button onClick={loadData} disabled={loading}>
                {loading ? <LoadingSpinner size="sm" className="me-2" /> : null}
                Apply
              </Button>
            </div>
          </CardContent>
        </Card>

        {loading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : (
          <>
            {/* Summary cards */}
            {summary && (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <SummaryCard
                  icon={DollarSign}
                  label="Total Revenue"
                  value={formatAmount(summary.total_revenue)}
                  color="text-green-500"
                />
                <SummaryCard
                  icon={Package}
                  label="Total COGS"
                  value={formatAmount(summary.total_cogs)}
                  color="text-orange-500"
                />
                <SummaryCard
                  icon={TrendingUp}
                  label="Gross Profit"
                  value={formatAmount(summary.gross_profit)}
                  detail={`${summary.gross_margin_pct}% margin`}
                  color="text-blue-500"
                />
                <SummaryCard
                  icon={ShoppingCart}
                  label="Net Profit"
                  value={formatAmount(summary.net_profit)}
                  detail={`${summary.total_orders} orders, ${formatAmount(summary.total_fees)} fees`}
                  color={Number(summary.net_profit) >= 0 ? "text-green-500" : "text-red-500"}
                />
              </div>
            )}

            {/* Item table */}
            <Card>
              <CardHeader>
                <CardTitle>Profitability by Item</CardTitle>
              </CardHeader>
              <CardContent>
                {items.length === 0 ? (
                  <p className="text-center text-muted-foreground py-8">
                    No items with activity found for this period.
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-muted-foreground">
                          <th className="py-3 text-start font-medium">Item</th>
                          <th className="py-3 text-end font-medium">Unit Price</th>
                          <th className="py-3 text-end font-medium">Unit Cost</th>
                          <th className="py-3 text-end font-medium">Revenue</th>
                          <th className="py-3 text-end font-medium">COGS</th>
                          <th className="py-3 text-end font-medium">Gross Profit</th>
                          <th className="py-3 text-end font-medium">Margin %</th>
                        </tr>
                      </thead>
                      <tbody>
                        {items.map((item) => (
                          <ItemRow key={item.code} item={item} formatAmount={formatAmount} />
                        ))}
                      </tbody>
                    </table>
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

function SummaryCard({
  icon: Icon,
  label,
  value,
  detail,
  color,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  detail?: string;
  color: string;
}) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-sm text-muted-foreground">{label}</p>
            <p className="text-2xl font-semibold mt-1">{value}</p>
            {detail && (
              <p className="text-xs text-muted-foreground mt-1">{detail}</p>
            )}
          </div>
          <div className={`rounded-lg p-2 bg-muted ${color}`}>
            <Icon className="h-5 w-5" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ItemRow({
  item,
  formatAmount,
}: {
  item: ItemProfitabilityRow;
  formatAmount: (v: string) => string;
}) {
  const margin = Number(item.margin_pct);
  const profit = Number(item.gross_profit);

  return (
    <tr className="border-b last:border-0 hover:bg-muted/50 transition-colors">
      <td className="py-3">
        <div>
          <span className="font-medium">{item.name}</span>
          <span className="text-xs text-muted-foreground ms-2">{item.code}</span>
        </div>
      </td>
      <td className="py-3 text-end font-mono">{formatAmount(item.unit_price)}</td>
      <td className="py-3 text-end font-mono">{formatAmount(item.unit_cost)}</td>
      <td className="py-3 text-end font-mono">{formatAmount(item.revenue)}</td>
      <td className="py-3 text-end font-mono">{formatAmount(item.cogs)}</td>
      <td className={`py-3 text-end font-mono ${profit >= 0 ? "text-green-500" : "text-red-500"}`}>
        {formatAmount(item.gross_profit)}
      </td>
      <td className="py-3 text-end">
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
            margin >= 30
              ? "bg-green-500/10 text-green-500"
              : margin >= 10
                ? "bg-yellow-500/10 text-yellow-500"
                : "bg-red-500/10 text-red-500"
          }`}
        >
          {margin >= 0 ? (
            <TrendingUp className="h-3 w-3" />
          ) : (
            <TrendingDown className="h-3 w-3" />
          )}
          {item.margin_pct}%
        </span>
      </td>
    </tr>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
