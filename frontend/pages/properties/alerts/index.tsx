import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useRouter } from "next/router";
import { AlertTriangle, Bell, Clock, DollarSign } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Badge } from "@/components/ui/badge";
import { PageHeader, EmptyState, LoadingSpinner } from "@/components/common";
import { usePropertyAlerts } from "@/queries/useProperties";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";

const SEVERITY_COLORS: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  warning: "bg-orange-100 text-orange-800",
  notice: "bg-blue-100 text-blue-800",
};

const TYPE_COLORS: Record<string, string> = {
  expiry: "bg-yellow-100 text-yellow-800",
  overdue: "bg-red-100 text-red-800",
};

export default function AlertsPage() {
  const router = useRouter();
  const { company } = useAuth();
  const cur = company?.default_currency || "USD";
  const { data: alerts, isLoading } = usePropertyAlerts();

  const expiryAlerts = alerts?.filter((a) => a.type === "expiry") ?? [];
  const overdueAlerts = alerts?.filter((a) => a.type === "overdue") ?? [];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Alerts"
          subtitle="Expiry warnings and overdue notices"
        />

        {isLoading ? (
          <LoadingSpinner />
        ) : !alerts?.length ? (
          <EmptyState
            icon={<Bell className="h-12 w-12" />}
            title="No alerts"
            description="All leases and payments are in good standing."
          />
        ) : (
          <div className="space-y-8">
            {/* Expiry Alerts */}
            {expiryAlerts.length > 0 && (
              <div>
                <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
                  <Clock className="h-5 w-5 text-yellow-500" />
                  Lease Expiry Warnings ({expiryAlerts.length})
                </h2>
                <div className="rounded-lg border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b bg-muted/50">
                        <th className="px-4 py-3 text-left font-medium">
                          Severity
                        </th>
                        <th className="px-4 py-3 text-left font-medium">
                          Contract
                        </th>
                        <th className="px-4 py-3 text-left font-medium">
                          Property
                        </th>
                        <th className="px-4 py-3 text-left font-medium">
                          Lessee
                        </th>
                        <th className="px-4 py-3 text-left font-medium">
                          End Date
                        </th>
                        <th className="px-4 py-3 text-right font-medium">
                          Days Left
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {expiryAlerts.map((alert, i) => (
                        <tr
                          key={`expiry-${i}`}
                          className="border-b cursor-pointer hover:bg-muted/30"
                          onClick={() =>
                            router.push(
                              `/properties/leases/${alert.lease_id}`
                            )
                          }
                        >
                          <td className="px-4 py-3">
                            <Badge
                              className={cn(
                                "text-xs",
                                SEVERITY_COLORS[alert.severity]
                              )}
                            >
                              {alert.severity}
                            </Badge>
                          </td>
                          <td className="px-4 py-3 font-medium">
                            {alert.contract_no}
                          </td>
                          <td className="px-4 py-3">
                            {alert.property_code} - {alert.property_name}
                          </td>
                          <td className="px-4 py-3">{alert.lessee_name}</td>
                          <td className="px-4 py-3">{alert.end_date}</td>
                          <td className="px-4 py-3 text-right font-mono">
                            {alert.days_until_expiry}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Overdue Alerts */}
            {overdueAlerts.length > 0 && (
              <div>
                <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
                  <AlertTriangle className="h-5 w-5 text-red-500" />
                  Overdue Notices ({overdueAlerts.length})
                </h2>
                <div className="rounded-lg border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b bg-muted/50">
                        <th className="px-4 py-3 text-left font-medium">
                          Severity
                        </th>
                        <th className="px-4 py-3 text-left font-medium">
                          Contract
                        </th>
                        <th className="px-4 py-3 text-left font-medium">
                          Property
                        </th>
                        <th className="px-4 py-3 text-left font-medium">
                          Lessee
                        </th>
                        <th className="px-4 py-3 text-left font-medium">
                          Installment
                        </th>
                        <th className="px-4 py-3 text-left font-medium">
                          Due Date
                        </th>
                        <th className="px-4 py-3 text-right font-medium">
                          Outstanding
                        </th>
                        <th className="px-4 py-3 text-right font-medium">
                          Days Overdue
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {overdueAlerts.map((alert, i) => (
                        <tr
                          key={`overdue-${i}`}
                          className="border-b cursor-pointer hover:bg-muted/30"
                          onClick={() =>
                            router.push(
                              `/properties/leases/${alert.lease_id}`
                            )
                          }
                        >
                          <td className="px-4 py-3">
                            <Badge
                              className={cn(
                                "text-xs",
                                SEVERITY_COLORS[alert.severity]
                              )}
                            >
                              {alert.severity}
                            </Badge>
                          </td>
                          <td className="px-4 py-3 font-medium">
                            {alert.contract_no}
                          </td>
                          <td className="px-4 py-3">
                            {alert.property_code} - {alert.property_name}
                          </td>
                          <td className="px-4 py-3">{alert.lessee_name}</td>
                          <td className="px-4 py-3 text-center">
                            #{alert.installment_no}
                          </td>
                          <td className="px-4 py-3">{alert.due_date}</td>
                          <td className="px-4 py-3 text-right">
                            {Number(alert.outstanding).toLocaleString()} {cur}
                          </td>
                          <td className="px-4 py-3 text-right font-mono text-red-600">
                            {alert.days_overdue}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
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
