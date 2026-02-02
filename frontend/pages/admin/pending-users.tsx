import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useState, useEffect, useCallback } from "react";
import { Check, X, RefreshCw } from "lucide-react";
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
import { getPendingApprovals, approveUser, rejectUser, type PendingUser } from "@/lib/api";
import { getAccessToken } from "@/lib/auth-storage";

export default function PendingUsersPage() {
  const { user } = useAuth();
  const [pendingUsers, setPendingUsers] = useState<PendingUser[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Reject dialog state
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<PendingUser | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const fetchPendingUsers = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const token = getAccessToken();
      if (!token) {
        setError("Not authenticated");
        return;
      }
      const data = await getPendingApprovals(token);
      setPendingUsers(data);
    } catch (err) {
      setError("Failed to load pending users");
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPendingUsers();
  }, [fetchPendingUsers]);

  const handleApprove = async (userId: number) => {
    try {
      setIsSubmitting(true);
      const token = getAccessToken();
      if (!token) return;
      await approveUser(token, userId);
      setPendingUsers((prev) => prev.filter((u) => u.id !== userId));
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
      const token = getAccessToken();
      if (!token) return;
      await rejectUser(token, selectedUser.id, rejectReason);
      setPendingUsers((prev) => prev.filter((u) => u.id !== selectedUser.id));
      setRejectDialogOpen(false);
      setSelectedUser(null);
    } catch (err) {
      console.error(err);
      setError("Failed to reject user");
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

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Pending User Approvals"
          subtitle="Review and approve new user registrations"
          actions={
            <Button variant="outline" onClick={fetchPendingUsers} disabled={isLoading}>
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

        <Card>
          <CardHeader>
            <CardTitle>Pending Registrations</CardTitle>
            <CardDescription>
              Users who have verified their email and are waiting for admin approval
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : pendingUsers.length === 0 ? (
              <EmptyState
                title="No pending approvals"
                description="All users have been reviewed. New registrations will appear here."
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
                        {pendingUser.email_verified ? (
                          <Badge variant="default">Verified</Badge>
                        ) : (
                          <Badge variant="secondary">Pending</Badge>
                        )}
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
                            disabled={isSubmitting || !pendingUser.email_verified}
                            title={!pendingUser.email_verified ? "User must verify email first" : "Approve user"}
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
