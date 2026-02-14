import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useState, useEffect } from "react";
import {
  User,
  Mail,
  Building2,
  KeyRound,
  Save,
  CheckCircle,
  Shield,
  ShieldCheck,
  Calendar,
  Eye,
  EyeOff,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useMutation } from "@tanstack/react-query";
import apiClient, { getErrorMessage } from "@/lib/api-client";

export default function ProfilePage() {
  const { t } = useTranslation(["common"]);
  const { user, company, refreshProfile } = useAuth();

  // Profile edit state
  const [name, setName] = useState(user?.name || "");
  const [nameAr, setNameAr] = useState(user?.name_ar || "");
  const [profileError, setProfileError] = useState("");
  const [profileSuccess, setProfileSuccess] = useState("");

  // Password change state
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [passwordSuccess, setPasswordSuccess] = useState("");

  // Reset password fields when component mounts (prevents browser autofill persistence)
  useEffect(() => {
    setNewPassword("");
    setConfirmPassword("");
    setShowNewPassword(false);
    setShowConfirmPassword(false);
  }, []);

  // Update profile mutation
  const updateProfileMutation = useMutation({
    mutationFn: async (data: { name?: string; name_ar?: string }) => {
      const response = await apiClient.patch(`/users/${user?.id}/`, data);
      return response.data;
    },
    onSuccess: () => {
      setProfileSuccess("Profile updated successfully!");
      setProfileError("");
      refreshProfile();
      setTimeout(() => setProfileSuccess(""), 3000);
    },
    onError: (error) => {
      setProfileError(getErrorMessage(error));
      setProfileSuccess("");
    },
  });

  // Change password mutation
  const changePasswordMutation = useMutation({
    mutationFn: async (password: string) => {
      const response = await apiClient.post(`/users/${user?.id}/set-password/`, {
        password,
      });
      return response.data;
    },
    onSuccess: () => {
      setPasswordSuccess("Password changed successfully!");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordError("");
      setTimeout(() => setPasswordSuccess(""), 3000);
    },
    onError: (error) => {
      setPasswordError(getErrorMessage(error));
      setPasswordSuccess("");
    },
  });

  const handleUpdateProfile = (e: React.FormEvent) => {
    e.preventDefault();
    setProfileError("");
    setProfileSuccess("");

    const updates: { name?: string; name_ar?: string } = {};
    if (name !== user?.name) updates.name = name;
    if (nameAr !== user?.name_ar) updates.name_ar = nameAr;

    if (Object.keys(updates).length === 0) {
      setProfileError("No changes to save");
      return;
    }

    updateProfileMutation.mutate(updates);
  };

  const handleChangePassword = (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError("");
    setPasswordSuccess("");

    if (!newPassword) {
      setPasswordError("Please enter a new password");
      return;
    }

    if (newPassword.length < 8) {
      setPasswordError("Password must be at least 8 characters");
      return;
    }

    if (newPassword !== confirmPassword) {
      setPasswordError("Passwords do not match");
      return;
    }

    changePasswordMutation.mutate(newPassword);
  };

  const formatDate = (dateStr: string | null | undefined) => {
    if (!dateStr) return "-";
    return new Date(dateStr).toLocaleDateString();
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("profile.title", "My Profile")}
          subtitle={t("profile.subtitle", "View and manage your account information")}
        />

        <div className="grid gap-6 lg:grid-cols-2">
          {/* User Info Card */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <User className="h-5 w-5" />
                {t("profile.accountInfo", "Account Information")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Email (read-only) */}
              <div className="flex items-center gap-3 p-3 rounded-lg bg-muted">
                <Mail className="h-5 w-5 text-muted-foreground" />
                <div className="flex-1">
                  <div className="text-sm text-muted-foreground">Email</div>
                  <div className="font-medium">{user?.email}</div>
                </div>
              </div>

              {/* Role/Status badges */}
              <div className="flex flex-wrap gap-2">
                {user?.is_superuser && (
                  <Badge variant="default" className="gap-1 bg-purple-600">
                    <ShieldCheck className="h-3 w-3" />
                    Superuser
                  </Badge>
                )}
                {user?.is_staff && !user?.is_superuser && (
                  <Badge variant="default" className="gap-1 bg-blue-600">
                    <Shield className="h-3 w-3" />
                    Staff
                  </Badge>
                )}
                <Badge variant="secondary" className="gap-1">
                  <Calendar className="h-3 w-3" />
                  Joined {formatDate(user?.created_at)}
                </Badge>
              </div>

              {/* Company info */}
              {company && (
                <div className="flex items-center gap-3 p-3 rounded-lg bg-muted">
                  <Building2 className="h-5 w-5 text-muted-foreground" />
                  <div className="flex-1">
                    <div className="text-sm text-muted-foreground">Active Company</div>
                    <div className="font-medium">{company.name}</div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Edit Profile Card */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Save className="h-5 w-5" />
                {t("profile.editProfile", "Edit Profile")}
              </CardTitle>
              <CardDescription>
                {t("profile.editProfileDesc", "Update your personal information")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleUpdateProfile} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="name">{t("profile.name", "Name")}</Label>
                  <Input
                    id="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Enter your name"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="name-ar">{t("profile.nameAr", "Name (Arabic)")}</Label>
                  <Input
                    id="name-ar"
                    value={nameAr}
                    onChange={(e) => setNameAr(e.target.value)}
                    placeholder="Enter your name in Arabic"
                    dir="rtl"
                  />
                </div>

                {profileError && (
                  <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
                    {profileError}
                  </div>
                )}

                {profileSuccess && (
                  <div className="p-3 rounded-lg bg-green-500/10 text-green-500 text-sm flex items-center gap-2">
                    <CheckCircle className="h-4 w-4" />
                    {profileSuccess}
                  </div>
                )}

                <Button
                  type="submit"
                  disabled={updateProfileMutation.isPending}
                >
                  {updateProfileMutation.isPending ? "Saving..." : t("profile.saveChanges", "Save Changes")}
                </Button>
              </form>
            </CardContent>
          </Card>

          {/* Change Password Card */}
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <KeyRound className="h-5 w-5" />
                {t("profile.changePassword", "Change Password")}
              </CardTitle>
              <CardDescription>
                {t("profile.changePasswordDesc", "Update your password to keep your account secure")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleChangePassword} className="space-y-4 max-w-md">
                <div className="space-y-2">
                  <Label htmlFor="new-password">
                    {t("profile.newPassword", "New Password")}
                  </Label>
                  <div className="relative">
                    <Input
                      id="new-password"
                      type={showNewPassword ? "text" : "password"}
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="Enter new password (min 8 characters)"
                      className="pr-10"
                      autoComplete="new-password"
                    />
                    <button
                      type="button"
                      onClick={() => setShowNewPassword(!showNewPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      {showNewPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="confirm-password">
                    {t("profile.confirmPassword", "Confirm Password")}
                  </Label>
                  <div className="relative">
                    <Input
                      id="confirm-password"
                      type={showConfirmPassword ? "text" : "password"}
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      placeholder="Confirm new password"
                      className="pr-10"
                      autoComplete="new-password"
                    />
                    <button
                      type="button"
                      onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      {showConfirmPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                {passwordError && (
                  <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
                    {passwordError}
                  </div>
                )}

                {passwordSuccess && (
                  <div className="p-3 rounded-lg bg-green-500/10 text-green-500 text-sm flex items-center gap-2">
                    <CheckCircle className="h-4 w-4" />
                    {passwordSuccess}
                  </div>
                )}

                <Button
                  type="submit"
                  disabled={changePasswordMutation.isPending}
                >
                  {changePasswordMutation.isPending ? "Changing..." : t("profile.updatePassword", "Update Password")}
                </Button>
              </form>
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
