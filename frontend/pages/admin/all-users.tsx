import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import {
  Users,
  Search,
  Building2,
  Calendar,
  Shield,
  ShieldCheck,
  ShieldAlert,
  Mail,
  Clock,
  KeyRound,
  X,
  Eye,
  EyeOff,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adminService, AdminUser } from "@/services/admin.service";
import { getErrorMessage } from "@/lib/api-client";

export default function AdminAllUsersPage() {
  const { t } = useTranslation(["common"]);
  const { user } = useAuth();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState("");

  // Password reset modal state
  const [resetPasswordUser, setResetPasswordUser] = useState<AdminUser | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [resetError, setResetError] = useState("");
  const [resetSuccess, setResetSuccess] = useState("");

  // Redirect non-superusers
  useEffect(() => {
    if (user && !user.is_superuser) {
      router.replace("/dashboard");
    }
  }, [user, router]);

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => adminService.getUsers(),
    enabled: user?.is_superuser,
  });

  const resetPasswordMutation = useMutation({
    mutationFn: ({ userId, password }: { userId: number; password: string }) =>
      adminService.resetPassword(userId, password),
    onSuccess: (data) => {
      setResetSuccess(`Password reset successfully for ${data.user_email}`);
      setNewPassword("");
      setTimeout(() => {
        setResetPasswordUser(null);
        setResetSuccess("");
      }, 2000);
    },
    onError: (error) => {
      setResetError(getErrorMessage(error));
    },
  });

  const handleResetPassword = () => {
    if (!resetPasswordUser || !newPassword) return;
    setResetError("");
    setResetSuccess("");

    if (newPassword.length < 8) {
      setResetError("Password must be at least 8 characters");
      return;
    }

    resetPasswordMutation.mutate({
      userId: resetPasswordUser.id,
      password: newPassword,
    });
  };

  const openResetModal = (u: AdminUser) => {
    setResetPasswordUser(u);
    setNewPassword("");
    setShowPassword(false);
    setResetError("");
    setResetSuccess("");
  };

  const closeResetModal = () => {
    setResetPasswordUser(null);
    setNewPassword("");
    setShowPassword(false);
    setResetError("");
    setResetSuccess("");
  };

  if (!user?.is_superuser) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center h-64">
          <p className="text-muted-foreground">Access denied. Superuser only.</p>
        </div>
      </AppLayout>
    );
  }

  const filteredUsers = data?.users?.filter(
    (u) =>
      u.email.toLowerCase().includes(searchQuery.toLowerCase()) ||
      u.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      u.primary_company?.toLowerCase().includes(searchQuery.toLowerCase())
  ) || [];

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "-";
    return new Date(dateStr).toLocaleDateString();
  };

  const formatDateTime = (dateStr: string | null) => {
    if (!dateStr) return "-";
    return new Date(dateStr).toLocaleString();
  };

  const getUserStatusBadges = (u: AdminUser) => {
    const badges = [];

    if (u.is_superuser) {
      badges.push(
        <Badge key="superuser" variant="default" className="gap-1 bg-purple-600">
          <ShieldCheck className="h-3 w-3" />
          Superuser
        </Badge>
      );
    } else if (u.is_staff) {
      badges.push(
        <Badge key="staff" variant="default" className="gap-1 bg-blue-600">
          <Shield className="h-3 w-3" />
          Staff
        </Badge>
      );
    }

    if (!u.email_verified) {
      badges.push(
        <Badge key="unverified" variant="destructive" className="gap-1">
          <Mail className="h-3 w-3" />
          Unverified
        </Badge>
      );
    }

    if (!u.is_approved && u.email_verified) {
      badges.push(
        <Badge key="pending" variant="outline" className="gap-1 border-yellow-500 text-yellow-500">
          <Clock className="h-3 w-3" />
          Pending
        </Badge>
      );
    }

    if (!u.is_active) {
      badges.push(
        <Badge key="inactive" variant="destructive" className="gap-1">
          <ShieldAlert className="h-3 w-3" />
          Inactive
        </Badge>
      );
    }

    return badges;
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="All Users"
          subtitle={`${data?.count || 0} users registered in the system`}
        />

        {/* Search */}
        <Card>
          <CardContent className="pt-6">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search users by email, name, or company..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-10"
              />
            </div>
          </CardContent>
        </Card>

        {/* Users Table */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Users className="h-5 w-5" />
              Users ({filteredUsers.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex justify-center py-8">
                <LoadingSpinner size="lg" />
              </div>
            ) : filteredUsers.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                No users found
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b">
                      <th className="text-start py-3 px-2 font-medium">User</th>
                      <th className="text-start py-3 px-2 font-medium">Company</th>
                      <th className="text-start py-3 px-2 font-medium">Status</th>
                      <th className="text-start py-3 px-2 font-medium">Joined</th>
                      <th className="text-start py-3 px-2 font-medium">Last Login</th>
                      <th className="text-start py-3 px-2 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredUsers.map((u) => (
                      <tr key={u.id} className="border-b hover:bg-muted/50">
                        <td className="py-3 px-2">
                          <div>
                            <div className="font-medium">{u.name || "-"}</div>
                            <div className="text-sm text-muted-foreground">
                              {u.email}
                            </div>
                            {u.name_ar && (
                              <div className="text-xs text-muted-foreground">
                                {u.name_ar}
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="py-3 px-2">
                          {u.primary_company ? (
                            <div className="flex items-center gap-2">
                              <Building2 className="h-4 w-4 text-muted-foreground" />
                              <div>
                                <div className="text-sm">{u.primary_company}</div>
                                {u.company_count > 1 && (
                                  <div className="text-xs text-muted-foreground">
                                    +{u.company_count - 1} more
                                  </div>
                                )}
                              </div>
                            </div>
                          ) : (
                            <span className="text-muted-foreground">-</span>
                          )}
                        </td>
                        <td className="py-3 px-2">
                          <div className="flex flex-wrap gap-1">
                            {getUserStatusBadges(u)}
                            {getUserStatusBadges(u).length === 0 && (
                              <Badge variant="secondary">User</Badge>
                            )}
                          </div>
                        </td>
                        <td className="py-3 px-2">
                          <div className="flex items-center gap-1 text-sm text-muted-foreground">
                            <Calendar className="h-3 w-3" />
                            {formatDate(u.date_joined)}
                          </div>
                        </td>
                        <td className="py-3 px-2">
                          <div className="text-sm text-muted-foreground">
                            {u.last_login ? formatDateTime(u.last_login) : "Never"}
                          </div>
                        </td>
                        <td className="py-3 px-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => openResetModal(u)}
                            className="gap-1"
                          >
                            <KeyRound className="h-3 w-3" />
                            Reset Password
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Password Reset Modal */}
        {resetPasswordUser && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
            <div className="w-full max-w-md rounded-lg border bg-card p-6 shadow-lg">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold flex items-center gap-2">
                  <KeyRound className="h-5 w-5" />
                  Reset Password
                </h3>
                <button
                  onClick={closeResetModal}
                  className="rounded-lg p-1 hover:bg-muted"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              <div className="space-y-4">
                <div>
                  <p className="text-sm text-muted-foreground mb-2">
                    Reset password for:
                  </p>
                  <div className="p-3 rounded-lg bg-muted">
                    <div className="font-medium">{resetPasswordUser.name || "No name"}</div>
                    <div className="text-sm text-muted-foreground">{resetPasswordUser.email}</div>
                  </div>
                </div>

                <div>
                  <label htmlFor="new-password" className="block text-sm font-medium mb-1">
                    New Password
                  </label>
                  <div className="relative">
                    <Input
                      id="new-password"
                      type={showPassword ? "text" : "password"}
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="Enter new password (min 8 characters)"
                      autoFocus
                      className="pr-10"
                      autoComplete="new-password"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                {resetError && (
                  <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
                    {resetError}
                  </div>
                )}

                {resetSuccess && (
                  <div className="p-3 rounded-lg bg-green-500/10 text-green-500 text-sm">
                    {resetSuccess}
                  </div>
                )}

                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={closeResetModal}>
                    Cancel
                  </Button>
                  <Button
                    onClick={handleResetPassword}
                    disabled={!newPassword || resetPasswordMutation.isPending}
                  >
                    {resetPasswordMutation.isPending ? "Resetting..." : "Reset Password"}
                  </Button>
                </div>
              </div>
            </div>
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
