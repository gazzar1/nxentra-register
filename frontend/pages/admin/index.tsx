import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect } from "react";
import {
  Users,
  Building2,
  Activity,
  UserCheck,
  Clock,
  Shield,
  TrendingUp,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useQuery } from "@tanstack/react-query";
import { adminService, AdminStats } from "@/services/admin.service";

export default function AdminDashboardPage() {
  const { t } = useTranslation(["common"]);
  const { user } = useAuth();
  const router = useRouter();

  // Redirect non-superusers
  useEffect(() => {
    if (user && !user.is_superuser) {
      router.replace("/dashboard");
    }
  }, [user, router]);

  const { data: stats, isLoading } = useQuery<AdminStats>({
    queryKey: ["admin", "stats"],
    queryFn: () => adminService.getStats(),
    enabled: user?.is_superuser,
  });

  if (!user?.is_superuser) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center h-64">
          <p className="text-muted-foreground">Access denied. Superuser only.</p>
        </div>
      </AppLayout>
    );
  }

  if (isLoading) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center h-64">
          <LoadingSpinner size="lg" />
        </div>
      </AppLayout>
    );
  }

  const statCards = [
    {
      label: "Total Users",
      value: stats?.total_users || 0,
      icon: <Users className="h-5 w-5" />,
      color: "text-blue-400",
    },
    {
      label: "Total Companies",
      value: stats?.total_companies || 0,
      icon: <Building2 className="h-5 w-5" />,
      color: "text-purple-400",
    },
    {
      label: "Active Users",
      value: stats?.active_users || 0,
      icon: <UserCheck className="h-5 w-5" />,
      color: "text-green-400",
    },
    {
      label: "Pending Approval",
      value: stats?.pending_approval || 0,
      icon: <Clock className="h-5 w-5" />,
      color: stats?.pending_approval ? "text-yellow-400" : "text-muted-foreground",
    },
    {
      label: "Verified Users",
      value: stats?.verified_users || 0,
      icon: <Shield className="h-5 w-5" />,
      color: "text-cyan-400",
    },
    {
      label: "Total Events",
      value: stats?.total_events || 0,
      icon: <Activity className="h-5 w-5" />,
      color: "text-orange-400",
    },
  ];

  const quickLinks = [
    {
      label: "Manage Companies",
      href: "/admin/companies",
      icon: <Building2 className="h-5 w-5" />,
      description: "View all companies in the system",
    },
    {
      label: "Manage Users",
      href: "/admin/all-users",
      icon: <Users className="h-5 w-5" />,
      description: "View all users in the system",
    },
    {
      label: "Audit Log",
      href: "/admin/audit-log",
      icon: <Activity className="h-5 w-5" />,
      description: "View all business events",
    },
    {
      label: "Pending Approvals",
      href: "/admin/pending-users",
      icon: <UserCheck className="h-5 w-5" />,
      description: "Approve new user registrations",
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Admin Dashboard"
          subtitle="System-wide administration and monitoring"
        />

        {/* Stats Grid */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {statCards.map((stat) => (
            <Card key={stat.label}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  {stat.label}
                </CardTitle>
                <div className={stat.color}>{stat.icon}</div>
              </CardHeader>
              <CardContent>
                <div className={`text-2xl font-bold ${stat.color}`}>
                  {stat.value.toLocaleString()}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Weekly Activity */}
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <TrendingUp className="h-5 w-5" />
                Weekly Activity
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">New Users (7 days)</span>
                  <span className="text-xl font-bold text-green-400">
                    +{stats?.new_users_week || 0}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">New Companies (7 days)</span>
                  <span className="text-xl font-bold text-purple-400">
                    +{stats?.new_companies_week || 0}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Quick Links */}
          <Card>
            <CardHeader>
              <CardTitle>Quick Actions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {quickLinks.map((link) => (
                <Link key={link.href} href={link.href}>
                  <Button
                    variant="outline"
                    className="w-full justify-start gap-3 h-auto py-3"
                  >
                    {link.icon}
                    <div className="text-start">
                      <div className="font-medium">{link.label}</div>
                      <div className="text-xs text-muted-foreground">
                        {link.description}
                      </div>
                    </div>
                  </Button>
                </Link>
              ))}
            </CardContent>
          </Card>
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
