import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useState, useEffect, useCallback } from "react";
import { Check, X, RefreshCw, Mail, Trash2 } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import {
  getPendingApprovals,
  approveUser,
  rejectUser,
  getUnverifiedUsers,
  resendVerificationEmail,
  deleteUnverifiedUser,
  type PendingUser,
  type UnverifiedUser,
} from "@/lib/api";
import { getAccessToken } from "@/lib/auth-storage";

export default function PendingUsersPage() {
  const { user } = useAuth();

  // Pending approvals state (verified email, waiting for approval)
  const [pendingUsers, setPendingUsers] = useState<PendingUser[]>([]);
  const [isPendingLoading, setIsPendingLoading] = useState(true);

  // Unverified users state (email not verified yet)
  const [unverifiedUsers, setUnverifiedUsers] = useState<UnverifiedUser[]>([]);
  const [isUnverifiedLoading, setIsUnverifiedLoading] = useState(true);

  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Reject dialog state
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<PendingUser | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  // Delete dialog state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [selectedUnverifiedUser, setSelectedUnverifiedUser] = useState<UnverifiedUser | null>(null);

  const [isSubmitting, setIsSubmitting] = useState(false);

  const fetchPendingUsers = useCallback(async () => {
    try {
      setIsPendingLoading(true);
      setError(null);
      const token = getAccessToken();
      if (!token) {
        setError("Not authenticated");
        return;
      }
      const data = await getPendingApprovals(token);
      setPendingUsers(data);
    } catch (err) {
      console.error(err);
      setError("Failed to load pending users");
    } finally {
      setIsPendingLoading(false);
    }
  }, []);

  const fetchUnverifiedUsers = useCallback(async () => {
    try {
      setIsUnverifiedLoading(true);
      const token = getAccessToken();
      if (!token) return;
      const data = await getUnverifiedUsers(token);
      setUnverifiedUsers(data);
    } catch (err) {
      console.error(err);
      // Don't show error for this, just log it
    } finally {
      setIsUnverifiedLoading(false);
    }
  }, []);

  const refreshAll = useCallback(() => {
    fetchPendingUsers();
    fetchUnverifiedUsers();
  }, [fetchPendingUsers, fetchUnverifiedUsers]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  // Clear success message after 5 seconds
  useEffect(() => {
    if (successMessage) {
      const timer = setTimeout(() => setSuccessMessage(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [successMessage]);

  const handleApprove = async (userId: number) => {
    try {
      setIsSubmitting(true);
      setError(null);
      const token = getAccessToken();
      if (!token) return;
      await approveUser(token, userId);
      setPendingUsers((prev) => prev.filter((u) => u.id !== userId));
      setSuccessMessage("User approved successfully");
    } catch (err) {
      console.error(err);
      setError("Failed to approve user");
    } finally {
      setIsSubmitting(false);
    }
  };

  const openRejectDialog = (pendingUser: PendingUser) => {
    setSelectedUser(pendingUser);
    setRejectReason("");
    setRejectDialogOpen(true);
  };

  const handleReject = async () => {
    if (!selectedUser) return;

    try {
      setIsSubmitting(true);
      setError(null);
      const token = getAccessToken();
      if (!token) return;
      await rejectUser(token, selectedUser.id, rejectReason);
      setPendingUsers((prev) => prev.filter((u) => u.id !== selectedUser.id));
      setRejectDialogOpen(false);
      setSelectedUser(null);
      setSuccessMessage("User rejected successfully");
    } catch (err) {
      console.error(err);
      setError("Failed to reject user");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleResendVerification = async (userId: number, email: string) => {
    try {
      setIsSubmitting(true);
      setError(null);
      const token = getAccessToken();
      if (!token) return;
      await resendVerificationEmail(token, userId);
      setSuccessMessage(`Verification email sent to ${email}`);
    } catch (err) {
      console.error(err);
      setError("Failed to send verification email");
    } finally {
      setIsSubmitting(false);
    }
  };

  const openDeleteDialog = (unverifiedUser: UnverifiedUser) => {
    setSelectedUnverifiedUser(unverifiedUser);
    setDeleteDialogOpen(true);
  };

  const handleDelete = async () => {
    if (!selectedUnverifiedUser) return;

    try {
      setIsSubmitting(true);
      setError(null);
      const token = getAccessToken();
      if (!token) return;
      await deleteUnverifiedUser(token, selectedUnverifiedUser.id);
      setUnverifiedUsers((prev) => prev.filter((u) => u.id !== selectedUnverifiedUser.id));
      setDeleteDialogOpen(false);
      setSelectedUnverifiedUser(null);
      setSuccessMessage("User deleted successfully");
    } catch (err) {
      console.error(err);
      setError("Failed to delete user");
    } finally {
      setIsSubmitting(false);
    }
  };

  const formatDate = (dateString: string | null) => {
    if (!dateString) return "—";
    return new Date(dateString).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  // Check if user is admin
  if (user && !user.is_staff && !user.is_superuser) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <Card className="max-w-md">
            <CardHeader>
              <CardTitle>Access Denied</CardTitle>
              <CardDescription>
                You do not have permission to access this page. Only administrators can approve user registrations.
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      </AppLayout>
    );
  }

  const isLoading = isPendingLoading || isUnverifiedLoading;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="User Management"
          subtitle="Review registrations, approve users, and manage unverified accounts"
          actions={
            <Button variant="outline" onClick={refreshAll} disabled={isLoading}>
              <RefreshCw className={`me-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          }
        />

        {error && (
          <Card className="border-destructive">
            <CardContent className="pt-6">
              <p className="text-destructive">{error}</p>
            </CardContent>
          </Card>
        )}

        {successMessage && (
          <Card className="border-green-500 bg-green-500/10">
            <CardContent className="pt-6">
              <p className="text-green-400">{successMessage}</p>
            </CardContent>
          </Card>
        )}

        {/* Pending Approvals Section */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Pending Approvals
              {pendingUsers.length > 0 && (
                <Badge variant="default">{pendingUsers.length}</Badge>
              )}
            </CardTitle>
            <CardDescription>
              Users who have verified their email and are waiting for admin approval
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isPendingLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : pendingUsers.length === 0 ? (
              <EmptyState
                title="No pending approvals"
                description="All verified users have been reviewed."
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>User</TableHead>
                    <TableHead>Company</TableHead>
                    <TableHead>Email Verified</TableHead>
                    <TableHead>Registered</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pendingUsers.map((pendingUser) => (
                    <TableRow key={pendingUser.id}>
                      <TableCell>
                        <div>
                          <p className="font-medium">{pendingUser.name || "—"}</p>
                          <p className="text-sm text-muted-foreground">{pendingUser.email}</p>
                        </div>
                      </TableCell>
                      <TableCell>{pendingUser.company_name || "—"}</TableCell>
                      <TableCell>
                        <Badge variant="default">Verified</Badge>
                        {pendingUser.email_verified_at && (
                          <p className="text-xs text-muted-foreground mt-1">
                            {formatDate(pendingUser.email_verified_at)}
                          </p>
                        )}
                      </TableCell>
                      <TableCell>{formatDate(pendingUser.date_joined)}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button
                            size="sm"
                            onClick={() => handleApprove(pendingUser.id)}
                            disabled={isSubmitting}
                          >
                            <Check className="me-1 h-4 w-4" />
                            Approve
                          </Button>
                          <Button
                            size="sm"
                            variant="destructive"
                            onClick={() => openRejectDialog(pendingUser)}
                            disabled={isSubmitting}
                          >
                            <X className="me-1 h-4 w-4" />
                            Reject
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* Unverified Users Section */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Unverified Users
              {unverifiedUsers.length > 0 && (
                <Badge variant="secondary">{unverifiedUsers.length}</Badge>
              )}
            </CardTitle>
            <CardDescription>
              Users who registered but haven&apos;t verified their email yet. You can resend verification emails or delete their accounts.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isUnverifiedLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : unverifiedUsers.length === 0 ? (
              <EmptyState
                title="No unverified users"
                description="All registered users have verified their email."
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>User</TableHead>
                    <TableHead>Company</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Registered</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {unverifiedUsers.map((unverifiedUser) => (
                    <TableRow key={unverifiedUser.id}>
                      <TableCell>
                        <div>
                          <p className="font-medium">{unverifiedUser.name || "—"}</p>
                          <p className="text-sm text-muted-foreground">{unverifiedUser.email}</p>
                        </div>
                      </TableCell>
                      <TableCell>{unverifiedUser.company_name || "—"}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-yellow-500 border-yellow-500">
                          Email Not Verified
                        </Badge>
                      </TableCell>
                      <TableCell>{formatDate(unverifiedUser.registered_at)}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => handleResendVerification(unverifiedUser.id, unverifiedUser.email)}
                            disabled={isSubmitting}
                            title="Resend verification email"
                          >
                            <Mail className="me-1 h-4 w-4" />
                            Resend
                          </Button>
                          <Button
                            size="sm"
                            variant="destructive"
                            onClick={() => openDeleteDialog(unverifiedUser)}
                            disabled={isSubmitting}
                            title="Delete user permanently"
                          >
                            <Trash2 className="me-1 h-4 w-4" />
                            Delete
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Reject Confirmation Dialog */}
      <Dialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject User Registration</DialogTitle>
            <DialogDescription>
              This will deactivate the user&apos;s account and send them a notification email.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div>
              <p className="text-sm text-muted-foreground">User:</p>
              <p className="font-medium">{selectedUser?.email}</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="reject-reason">Reason for rejection (optional)</Label>
              <Textarea
                id="reject-reason"
                placeholder="Provide a reason that will be included in the notification email..."
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectDialogOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleReject} disabled={isSubmitting}>
              {isSubmitting ? "Rejecting..." : "Reject User"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Unverified User</DialogTitle>
            <DialogDescription>
              This will permanently delete the user account and their associated company. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div>
              <p className="text-sm text-muted-foreground">User:</p>
              <p className="font-medium">{selectedUnverifiedUser?.email}</p>
            </div>
            {selectedUnverifiedUser?.company_name && (
              <div>
                <p className="text-sm text-muted-foreground">Company:</p>
                <p className="font-medium">{selectedUnverifiedUser.company_name}</p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={isSubmitting}>
              {isSubmitting ? "Deleting..." : "Delete Permanently"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "settings"])),
    },
  };
};
