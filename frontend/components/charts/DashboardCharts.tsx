import { useTranslation } from "next-i18next";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Area,
  AreaChart,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  MonthlyRevenueExpenses,
  AccountTypeDistribution,
  MonthlyNetIncome,
  TopAccount,
} from "@/types/report";

// Color palette
const COLORS = ["#0ea5e9", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6"];
const REVENUE_COLOR = "#22c55e";
const EXPENSE_COLOR = "#ef4444";
const NET_INCOME_POSITIVE = "#22c55e";

interface RevenueExpensesChartProps {
  data: MonthlyRevenueExpenses[];
  compact?: boolean;
}

export function RevenueExpensesChart({ data, compact }: RevenueExpensesChartProps) {
  const { t } = useTranslation(["reports"]);
  const chartHeight = compact ? 180 : 300;

  const formatCurrency = (value: number) => {
    if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `${(value / 1000).toFixed(0)}K`;
    return value.toFixed(0);
  };

  return (
    <Card>
      <CardHeader className={compact ? "pb-2" : ""}>
        <CardTitle className={compact ? "text-sm" : "text-base"}>
          {t("reports:charts.revenueVsExpenses", "Revenue vs Expenses")}
        </CardTitle>
      </CardHeader>
      <CardContent className={compact ? "pt-0" : ""}>
        <ResponsiveContainer width="100%" height={chartHeight}>
          <BarChart data={data} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey="month"
              stroke="#9ca3af"
              fontSize={compact ? 10 : 12}
              interval={compact ? 1 : 0}
            />
            <YAxis
              stroke="#9ca3af"
              fontSize={compact ? 10 : 12}
              tickFormatter={formatCurrency}
              width={compact ? 35 : 50}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "#1f2937",
                border: "1px solid #374151",
                borderRadius: "8px",
                fontSize: "12px",
              }}
              labelStyle={{ color: "#f3f4f6" }}
              formatter={(value) => value != null ? formatCurrency(Number(value)) : ''}
            />
            {!compact && <Legend />}
            <Bar
              dataKey="revenue"
              name={t("reports:charts.revenue", "Revenue")}
              fill={REVENUE_COLOR}
              radius={[2, 2, 0, 0]}
            />
            <Bar
              dataKey="expenses"
              name={t("reports:charts.expenses", "Expenses")}
              fill={EXPENSE_COLOR}
              radius={[2, 2, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

interface AccountDistributionChartProps {
  data: AccountTypeDistribution[];
  compact?: boolean;
}

export function AccountDistributionChart({ data, compact }: AccountDistributionChartProps) {
  const { t } = useTranslation(["reports"]);
  const chartHeight = compact ? 180 : 300;
  const outerRadius = compact ? 60 : 100;
  const innerRadius = compact ? 30 : 50;

  const formatCurrency = (value: number) => {
    if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `${(value / 1000).toFixed(0)}K`;
    return value.toFixed(0);
  };

  const CustomLabel = ({
    cx,
    cy,
    midAngle,
    innerRadius: ir,
    outerRadius: or,
    percent,
  }: {
    cx?: number;
    cy?: number;
    midAngle?: number;
    innerRadius?: number;
    outerRadius?: number;
    percent?: number;
  }) => {
    if (cx === undefined || cy === undefined || midAngle === undefined ||
        ir === undefined || or === undefined || percent === undefined) {
      return null;
    }
    const RADIAN = Math.PI / 180;
    const radius = ir + (or - ir) * 0.5;
    const x = cx + radius * Math.cos(-midAngle * RADIAN);
    const y = cy + radius * Math.sin(-midAngle * RADIAN);

    if (percent < 0.08) return null;

    return (
      <text
        x={x}
        y={y}
        fill="white"
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={compact ? 9 : 12}
      >
        {`${(percent * 100).toFixed(0)}%`}
      </text>
    );
  };

  return (
    <Card>
      <CardHeader className={compact ? "pb-2" : ""}>
        <CardTitle className={compact ? "text-sm" : "text-base"}>
          {t("reports:charts.accountDistribution", "Balance by Type")}
        </CardTitle>
      </CardHeader>
      <CardContent className={compact ? "pt-0" : ""}>
        <ResponsiveContainer width="100%" height={chartHeight}>
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy={compact ? "45%" : "50%"}
              labelLine={false}
              label={CustomLabel}
              outerRadius={outerRadius}
              innerRadius={innerRadius}
              fill="#8884d8"
              dataKey="value"
              paddingAngle={2}
            >
              {data.map((entry, index) => (
                <Cell
                  key={`cell-${index}`}
                  fill={COLORS[index % COLORS.length]}
                />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                backgroundColor: "#1f2937",
                border: "1px solid #374151",
                borderRadius: "8px",
                fontSize: "12px",
              }}
              formatter={(value) => value != null ? formatCurrency(Number(value)) : ''}
            />
            <Legend
              wrapperStyle={{ fontSize: compact ? "10px" : "12px" }}
              iconSize={compact ? 8 : 14}
            />
          </PieChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

interface NetIncomeTrendChartProps {
  data: MonthlyNetIncome[];
  compact?: boolean;
}

export function NetIncomeTrendChart({ data, compact }: NetIncomeTrendChartProps) {
  const { t } = useTranslation(["reports"]);
  const chartHeight = compact ? 180 : 300;

  const formatCurrency = (value: number) => {
    if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `${(value / 1000).toFixed(0)}K`;
    if (value <= -1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (value <= -1000) return `${(value / 1000).toFixed(0)}K`;
    return value.toFixed(0);
  };

  const chartData = data.map((item) => ({
    ...item,
    positive: item.net_income > 0 ? item.net_income : 0,
    negative: item.net_income < 0 ? item.net_income : 0,
  }));

  return (
    <Card>
      <CardHeader className={compact ? "pb-2" : ""}>
        <CardTitle className={compact ? "text-sm" : "text-base"}>
          {t("reports:charts.netIncomeTrend", "Net Income Trend")}
        </CardTitle>
      </CardHeader>
      <CardContent className={compact ? "pt-0" : ""}>
        <ResponsiveContainer width="100%" height={chartHeight}>
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey="month"
              stroke="#9ca3af"
              fontSize={compact ? 10 : 12}
              interval={compact ? 1 : 0}
            />
            <YAxis
              stroke="#9ca3af"
              fontSize={compact ? 10 : 12}
              tickFormatter={formatCurrency}
              width={compact ? 35 : 50}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "#1f2937",
                border: "1px solid #374151",
                borderRadius: "8px",
                fontSize: "12px",
              }}
              labelStyle={{ color: "#f3f4f6" }}
              formatter={(value) => value != null ? formatCurrency(Number(value)) : ''}
            />
            <defs>
              <linearGradient id="colorPositive" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={NET_INCOME_POSITIVE} stopOpacity={0.8} />
                <stop offset="95%" stopColor={NET_INCOME_POSITIVE} stopOpacity={0.1} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="net_income"
              stroke={NET_INCOME_POSITIVE}
              fill="url(#colorPositive)"
              name={t("reports:charts.netIncome", "Net Income")}
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

interface TopAccountsChartProps {
  data: TopAccount[];
  compact?: boolean;
}

export function TopAccountsChart({ data, compact }: TopAccountsChartProps) {
  const { t } = useTranslation(["reports"]);
  const chartHeight = compact ? 180 : 300;

  const formatCurrency = (value: number) => {
    if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `${(value / 1000).toFixed(0)}K`;
    return value.toFixed(0);
  };

  // Take fewer items in compact mode, reverse for bottom-to-top ranking
  const chartData = compact
    ? [...data].slice(0, 5).reverse()
    : [...data].reverse();

  return (
    <Card>
      <CardHeader className={compact ? "pb-2" : ""}>
        <CardTitle className={compact ? "text-sm" : "text-base"}>
          {t("reports:charts.topAccounts", "Top Accounts")}
        </CardTitle>
      </CardHeader>
      <CardContent className={compact ? "pt-0" : ""}>
        <ResponsiveContainer width="100%" height={chartHeight}>
          <BarChart
            data={chartData}
            layout="vertical"
            margin={{ top: 5, right: 10, left: compact ? 50 : 80, bottom: 5 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" horizontal={false} />
            <XAxis
              type="number"
              stroke="#9ca3af"
              fontSize={compact ? 10 : 12}
              tickFormatter={formatCurrency}
            />
            <YAxis
              type="category"
              dataKey="name"
              stroke="#9ca3af"
              fontSize={compact ? 9 : 11}
              width={compact ? 50 : 80}
              tick={{ fill: "#9ca3af" }}
              tickFormatter={(value: string) =>
                compact && value.length > 8 ? `${value.slice(0, 8)}...` : value
              }
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "#1f2937",
                border: "1px solid #374151",
                borderRadius: "8px",
                fontSize: "12px",
              }}
              labelStyle={{ color: "#f3f4f6" }}
              formatter={(value) => value != null ? [
                formatCurrency(Number(value)),
                t("reports:charts.activity", "Activity"),
              ] : ''}
            />
            <Bar
              dataKey="total_activity"
              fill="#0ea5e9"
              radius={[0, 4, 4, 0]}
              name={t("reports:charts.totalActivity", "Total Activity")}
            />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
