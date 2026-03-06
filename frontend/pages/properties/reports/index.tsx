import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useState } from "react";
import {
  BarChart3,
  FileText,
  Building2,
  DoorOpen,
  DollarSign,
  AlertTriangle,
  Receipt,
  Shield,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, LoadingSpinner } from "@/components/common";
import {
  useRentRollReport,
  useOverdueReport,
  useExpiryReport,
  useOccupancyReport,
  useIncomeReport,
  useCollectionsReport,
  useExpenseBreakdownReport,
  useDepositLiabilityReport,
} from "@/queries/useProperties";
import { cn } from "@/lib/cn";

type ReportTab =
  | "rent-roll"
  | "overdue"
  | "expiry"
  | "occupancy"
  | "income"
  | "collections"
  | "expenses"
  | "deposits";

const TABS: { key: ReportTab; label: string; icon: React.ReactNode }[] = [
  { key: "rent-roll", label: "Rent Roll", icon: <FileText className="h-4 w-4" /> },
  { key: "overdue", label: "Overdue", icon: <AlertTriangle className="h-4 w-4" /> },
  { key: "expiry", label: "Lease Expiry", icon: <FileText className="h-4 w-4" /> },
  { key: "occupancy", label: "Occupancy", icon: <DoorOpen className="h-4 w-4" /> },
  { key: "income", label: "Net Income", icon: <DollarSign className="h-4 w-4" /> },
  { key: "collections", label: "Collections", icon: <BarChart3 className="h-4 w-4" /> },
  { key: "expenses", label: "Expenses", icon: <Receipt className="h-4 w-4" /> },
  { key: "deposits", label: "Deposits", icon: <Shield className="h-4 w-4" /> },
];

const CATEGORY_LABELS: Record<string, string> = {
  maintenance: "Maintenance",
  utilities: "Utilities",
  cleaning: "Cleaning",
  security: "Security",
  salary: "Salary",
  tax: "Tax",
  insurance: "Insurance",
  legal: "Legal",
  marketing: "Marketing",
  other: "Other",
};

export default function PropertyReportsPage() {
  const [activeTab, setActiveTab] = useState<ReportTab>("rent-roll");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [expiryDays, setExpiryDays] = useState<number>(90);

  const dateParams =
    dateFrom || dateTo
      ? { date_from: dateFrom || undefined, date_to: dateTo || undefined }
      : undefined;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Property Reports"
          subtitle="Financial and operational property reports"
        />

        {/* Tab Navigation */}
        <div className="flex flex-wrap gap-2">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={cn(
                "inline-flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                activeTab === tab.key
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted hover:bg-muted/80 text-muted-foreground"
              )}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>

        {/* Date Filters (for applicable reports) */}
        {["income", "collections", "expenses"].includes(activeTab) && (
          <div className="flex gap-4 items-end">
            <div className="space-y-1">
              <Label className="text-xs">From</Label>
              <Input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="w-40"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">To</Label>
              <Input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="w-40"
              />
            </div>
          </div>
        )}

        {activeTab === "expiry" && (
          <div className="flex gap-4 items-end">
            <div className="space-y-1">
              <Label className="text-xs">Threshold (days)</Label>
              <Select
                value={String(expiryDays)}
                onValueChange={(v) => setExpiryDays(Number(v))}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="30">30 days</SelectItem>
                  <SelectItem value="60">60 days</SelectItem>
                  <SelectItem value="90">90 days</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        )}

        {/* Report Content */}
        {activeTab === "rent-roll" && <RentRollReport />}
        {activeTab === "overdue" && <OverdueReport />}
        {activeTab === "expiry" && <ExpiryReport days={expiryDays} />}
        {activeTab === "occupancy" && <OccupancyReport />}
        {activeTab === "income" && <IncomeReport params={dateParams} />}
        {activeTab === "collections" && <CollectionsReport params={dateParams} />}
        {activeTab === "expenses" && <ExpensesReport params={dateParams} />}
        {activeTab === "deposits" && <DepositsReport />}
      </div>
    </AppLayout>
  );
}

function RentRollReport() {
  const { data, isLoading } = useRentRollReport();
  if (isLoading) return <LoadingSpinner />;
  if (!data?.length)
    return <p className="text-sm text-muted-foreground">No active leases.</p>;

  return (
    <div className="rounded-lg border overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="px-4 py-3 text-left font-medium">Contract</th>
            <th className="px-4 py-3 text-left font-medium">Property</th>
            <th className="px-4 py-3 text-left font-medium">Unit</th>
            <th className="px-4 py-3 text-left font-medium">Lessee</th>
            <th className="px-4 py-3 text-left font-medium">Period</th>
            <th className="px-4 py-3 text-right font-medium">Rent</th>
            <th className="px-4 py-3 text-right font-medium">Billed</th>
            <th className="px-4 py-3 text-right font-medium">Collected</th>
            <th className="px-4 py-3 text-right font-medium">Outstanding</th>
            <th className="px-4 py-3 text-center font-medium">Overdue</th>
          </tr>
        </thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.lease_id} className="border-b">
              <td className="px-4 py-3 font-medium">{r.contract_no}</td>
              <td className="px-4 py-3">{r.property_code}</td>
              <td className="px-4 py-3">{r.unit_code}</td>
              <td className="px-4 py-3">{r.lessee_name}</td>
              <td className="px-4 py-3 text-xs">
                {r.start_date} — {r.end_date}
              </td>
              <td className="px-4 py-3 text-right">
                {Number(r.rent_amount).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-right">
                {Number(r.total_billed).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-right">
                {Number(r.total_collected).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-right font-medium">
                {Number(r.total_outstanding).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-center">
                {r.overdue_count > 0 && (
                  <Badge className="bg-red-100 text-red-800 text-xs">
                    {r.overdue_count}
                  </Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OverdueReport() {
  const { data, isLoading } = useOverdueReport();
  if (isLoading) return <LoadingSpinner />;
  if (!data?.length)
    return (
      <p className="text-sm text-muted-foreground">No overdue balances.</p>
    );

  return (
    <div className="space-y-4">
      {data.map((lessee) => (
        <Card key={lessee.lessee_id}>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <div className="font-semibold">{lessee.lessee_name}</div>
                <div className="text-xs text-muted-foreground">
                  {lessee.lessee_code}
                </div>
              </div>
              <div className="text-right">
                <div className="text-lg font-bold text-red-600">
                  {Number(lessee.total_overdue).toLocaleString()} SAR
                </div>
                <div className="text-xs text-muted-foreground">
                  {lessee.overdue_count} overdue installments
                </div>
              </div>
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b">
                  <th className="py-1 text-left">Contract</th>
                  <th className="py-1 text-left">Property</th>
                  <th className="py-1 text-center">Installment</th>
                  <th className="py-1 text-left">Due Date</th>
                  <th className="py-1 text-right">Outstanding</th>
                </tr>
              </thead>
              <tbody>
                {lessee.lines.map((line, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td className="py-1">{line.contract_no}</td>
                    <td className="py-1">{line.property_code}</td>
                    <td className="py-1 text-center">#{line.installment_no}</td>
                    <td className="py-1">{line.due_date}</td>
                    <td className="py-1 text-right">
                      {Number(line.outstanding).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ExpiryReport({ days }: { days: number }) {
  const { data, isLoading } = useExpiryReport(days);
  if (isLoading) return <LoadingSpinner />;
  if (!data?.length)
    return (
      <p className="text-sm text-muted-foreground">
        No leases expiring within {days} days.
      </p>
    );

  const URGENCY_COLORS: Record<string, string> = {
    critical: "bg-red-100 text-red-800",
    warning: "bg-orange-100 text-orange-800",
    notice: "bg-blue-100 text-blue-800",
  };

  return (
    <div className="rounded-lg border overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="px-4 py-3 text-left font-medium">Urgency</th>
            <th className="px-4 py-3 text-left font-medium">Contract</th>
            <th className="px-4 py-3 text-left font-medium">Property</th>
            <th className="px-4 py-3 text-left font-medium">Lessee</th>
            <th className="px-4 py-3 text-left font-medium">End Date</th>
            <th className="px-4 py-3 text-right font-medium">Days Left</th>
            <th className="px-4 py-3 text-right font-medium">Rent</th>
          </tr>
        </thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.lease_id} className="border-b">
              <td className="px-4 py-3">
                <Badge
                  className={cn("text-xs", URGENCY_COLORS[r.urgency])}
                >
                  {r.urgency}
                </Badge>
              </td>
              <td className="px-4 py-3 font-medium">{r.contract_no}</td>
              <td className="px-4 py-3">{r.property_code}</td>
              <td className="px-4 py-3">{r.lessee_name}</td>
              <td className="px-4 py-3">{r.end_date}</td>
              <td className="px-4 py-3 text-right font-mono">
                {r.days_until_expiry}
              </td>
              <td className="px-4 py-3 text-right">
                {Number(r.rent_amount).toLocaleString()} {r.currency}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OccupancyReport() {
  const { data, isLoading } = useOccupancyReport();
  if (isLoading) return <LoadingSpinner />;
  if (!data?.length)
    return <p className="text-sm text-muted-foreground">No properties.</p>;

  return (
    <div className="rounded-lg border overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="px-4 py-3 text-left font-medium">Property</th>
            <th className="px-4 py-3 text-left font-medium">Type</th>
            <th className="px-4 py-3 text-center font-medium">Total</th>
            <th className="px-4 py-3 text-center font-medium">Occupied</th>
            <th className="px-4 py-3 text-center font-medium">Vacant</th>
            <th className="px-4 py-3 text-center font-medium">Other</th>
            <th className="px-4 py-3 text-right font-medium">Occupancy</th>
          </tr>
        </thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.property_id} className="border-b">
              <td className="px-4 py-3 font-medium">
                {r.property_code} - {r.property_name}
              </td>
              <td className="px-4 py-3 capitalize">
                {r.property_type.replace(/_/g, " ")}
              </td>
              <td className="px-4 py-3 text-center">{r.total_units}</td>
              <td className="px-4 py-3 text-center text-green-600">
                {r.occupied}
              </td>
              <td className="px-4 py-3 text-center text-orange-600">
                {r.vacant}
              </td>
              <td className="px-4 py-3 text-center text-gray-500">
                {r.maintenance}
              </td>
              <td className="px-4 py-3 text-right font-mono">
                {r.occupancy_rate}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IncomeReport({
  params,
}: {
  params?: { date_from?: string; date_to?: string };
}) {
  const { data, isLoading } = useIncomeReport(params);
  if (isLoading) return <LoadingSpinner />;
  if (!data?.length)
    return <p className="text-sm text-muted-foreground">No income data.</p>;

  return (
    <div className="rounded-lg border overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="px-4 py-3 text-left font-medium">Property</th>
            <th className="px-4 py-3 text-right font-medium">Income</th>
            <th className="px-4 py-3 text-right font-medium">Expenses</th>
            <th className="px-4 py-3 text-right font-medium">Net Income</th>
          </tr>
        </thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.property_id} className="border-b">
              <td className="px-4 py-3 font-medium">
                {r.property_code} - {r.property_name}
              </td>
              <td className="px-4 py-3 text-right text-green-600">
                {Number(r.total_income).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-right text-red-600">
                {Number(r.total_expenses).toLocaleString()}
              </td>
              <td
                className={cn(
                  "px-4 py-3 text-right font-bold",
                  Number(r.net_income) >= 0
                    ? "text-green-600"
                    : "text-red-600"
                )}
              >
                {Number(r.net_income).toLocaleString()} {r.currency}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CollectionsReport({
  params,
}: {
  params?: { date_from?: string; date_to?: string };
}) {
  const { data, isLoading } = useCollectionsReport(params);
  if (isLoading) return <LoadingSpinner />;
  if (!data?.length)
    return (
      <p className="text-sm text-muted-foreground">No collections data.</p>
    );

  return (
    <div className="rounded-lg border overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50">
            <th className="px-4 py-3 text-left font-medium">Property</th>
            <th className="px-4 py-3 text-right font-medium">Billed</th>
            <th className="px-4 py-3 text-right font-medium">Collected</th>
            <th className="px-4 py-3 text-right font-medium">Outstanding</th>
            <th className="px-4 py-3 text-right font-medium">Rate</th>
          </tr>
        </thead>
        <tbody>
          {data.map((r, i) => (
            <tr key={i} className="border-b">
              <td className="px-4 py-3 font-medium">
                {r.property_code} - {r.property_name}
              </td>
              <td className="px-4 py-3 text-right">
                {Number(r.total_billed).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-right text-green-600">
                {Number(r.total_collected).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-right text-orange-600">
                {Number(r.outstanding).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-right font-mono">
                {r.collection_rate}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExpensesReport({
  params,
}: {
  params?: { date_from?: string; date_to?: string };
}) {
  const { data, isLoading } = useExpenseBreakdownReport(params);
  if (isLoading) return <LoadingSpinner />;
  if (!data)
    return <p className="text-sm text-muted-foreground">No expense data.</p>;

  return (
    <div className="space-y-6">
      {/* By Property */}
      {data.by_property.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold mb-2">By Property</h3>
          <div className="rounded-lg border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium">Property</th>
                  <th className="px-4 py-3 text-right font-medium">Total</th>
                </tr>
              </thead>
              <tbody>
                {data.by_property.map((r, i) => (
                  <tr key={i} className="border-b">
                    <td className="px-4 py-3">
                      {r.property_code} - {r.property_name}
                    </td>
                    <td className="px-4 py-3 text-right font-medium">
                      {Number(r.total).toLocaleString()} SAR
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* By Category */}
      {data.by_category.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold mb-2">By Category</h3>
          <div className="rounded-lg border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium">Category</th>
                  <th className="px-4 py-3 text-right font-medium">Total</th>
                </tr>
              </thead>
              <tbody>
                {data.by_category.map((r, i) => (
                  <tr key={i} className="border-b">
                    <td className="px-4 py-3">
                      {CATEGORY_LABELS[r.category] || r.category}
                    </td>
                    <td className="px-4 py-3 text-right font-medium">
                      {Number(r.total).toLocaleString()} SAR
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function DepositsReport() {
  const { data, isLoading } = useDepositLiabilityReport();
  if (isLoading) return <LoadingSpinner />;
  if (!data?.leases?.length)
    return <p className="text-sm text-muted-foreground">No deposit data.</p>;

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="pt-4">
          <div className="text-sm text-muted-foreground">
            Total Deposit Liability
          </div>
          <div className="text-2xl font-bold">
            {Number(data.total_liability).toLocaleString()} SAR
          </div>
        </CardContent>
      </Card>

      <div className="rounded-lg border overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="px-4 py-3 text-left font-medium">Contract</th>
              <th className="px-4 py-3 text-left font-medium">Property</th>
              <th className="px-4 py-3 text-left font-medium">Lessee</th>
              <th className="px-4 py-3 text-center font-medium">Status</th>
              <th className="px-4 py-3 text-right font-medium">Received</th>
              <th className="px-4 py-3 text-right font-medium">Refunded</th>
              <th className="px-4 py-3 text-right font-medium">Balance</th>
            </tr>
          </thead>
          <tbody>
            {data.leases.map((r) => (
              <tr key={r.lease_id} className="border-b">
                <td className="px-4 py-3 font-medium">{r.contract_no}</td>
                <td className="px-4 py-3">{r.property_code}</td>
                <td className="px-4 py-3">{r.lessee_name}</td>
                <td className="px-4 py-3 text-center">
                  <Badge className="text-xs">{r.lease_status}</Badge>
                </td>
                <td className="px-4 py-3 text-right">
                  {Number(r.deposit_received).toLocaleString()}
                </td>
                <td className="px-4 py-3 text-right">
                  {Number(r.deposit_refunded).toLocaleString()}
                </td>
                <td className="px-4 py-3 text-right font-bold">
                  {Number(r.current_balance).toLocaleString()} {r.currency}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
