import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import {
  Building2,
  DoorOpen,
  FileSignature,
  AlertTriangle,
  DollarSign,
  TrendingUp,
  TrendingDown,
  Clock,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { usePropertyDashboard } from "@/queries/useProperties";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import { useRouter } from "next/router";

export default function PropertyDashboardPage() {
  const { data: dashboard, isLoading } = usePropertyDashboard();
  const { company } = useAuth();
  const router = useRouter();
  const cur = company?.default_currency || "USD";

  if (isLoading) {
    return (
      <AppLayout>
        <LoadingSpinner />
      </AppLayout>
    );
  }

  if (!dashboard) {
    return (
      <AppLayout>
        <PageHeader title="Property Dashboard" subtitle="Overview of property operations" />
        <p className="text-muted-foreground text-sm mt-4">No data available.</p>
      </AppLayout>
    );
  }

  const cards = [
    {
      label: "Active Leases",
      value: dashboard.active_leases,
      icon: <FileSignature className="h-5 w-5 text-blue-500" />,
      onClick: () => router.push("/properties/leases"),
    },
    {
      label: "Properties",
      value: dashboard.total_properties,
      icon: <Building2 className="h-5 w-5 text-amber-500" />,
      onClick: () => router.push("/properties/properties"),
    },
    {
      label: "Occupancy Rate",
      value: `${dashboard.occupancy_rate}%`,
      sub: `${dashboard.occupied_units} / ${dashboard.total_units} units`,
      icon: <DoorOpen className="h-5 w-5 text-green-500" />,
    },
    {
      label: "Expiring (90 days)",
      value: dashboard.expiring_leases_90d,
      icon: <Clock className="h-5 w-5 text-yellow-500" />,
      onClick: () => router.push("/properties/alerts"),
      alert: dashboard.expiring_leases_90d > 0,
    },
    {
      label: "Total Overdue",
      value: `${Number(dashboard.total_overdue).toLocaleString()} ${cur}`,
      sub: `${dashboard.overdue_count} installments`,
      icon: <AlertTriangle className="h-5 w-5 text-red-500" />,
      alert: dashboard.overdue_count > 0,
    },
    {
      label: "Monthly Billed",
      value: `${Number(dashboard.monthly_billed).toLocaleString()} ${cur}`,
      icon: <DollarSign className="h-5 w-5 text-blue-500" />,
    },
    {
      label: "Monthly Collected",
      value: `${Number(dashboard.monthly_collected).toLocaleString()} ${cur}`,
      icon: <TrendingUp className="h-5 w-5 text-green-500" />,
    },
    {
      label: "Monthly Expenses",
      value: `${Number(dashboard.monthly_expenses).toLocaleString()} ${cur}`,
      icon: <TrendingDown className="h-5 w-5 text-red-400" />,
      onClick: () => router.push("/properties/expenses"),
    },
    {
      label: "Deposit Liability",
      value: `${Number(dashboard.deposit_liability).toLocaleString()} ${cur}`,
      icon: <DollarSign className="h-5 w-5 text-purple-500" />,
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Property Dashboard"
          subtitle="Overview of property operations"
        />

        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
          {cards.map((card) => (
            <Card
              key={card.label}
              className={cn(
                "transition-shadow",
                card.onClick && "cursor-pointer hover:shadow-md",
                card.alert && "border-yellow-300"
              )}
              onClick={card.onClick}
            >
              <CardContent className="pt-4">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm text-muted-foreground">
                      {card.label}
                    </div>
                    <div className="text-2xl font-bold mt-1">{card.value}</div>
                    {card.sub && (
                      <div className="text-xs text-muted-foreground mt-1">
                        {card.sub}
                      </div>
                    )}
                  </div>
                  <div className="p-2 bg-muted rounded-lg">{card.icon}</div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
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
