import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Mic, MicOff, RefreshCw, Plus, Settings2 } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
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
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { voiceService, VoiceUserStatus } from "@/services/users.service";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import { useAuth } from "@/contexts/AuthContext";

export default function VoiceSettingsPage() {
  const { t } = useTranslation(["common", "settings"]);
  const { toast } = useToast();
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();

  const canManageVoice = hasPermission("voice.admin");

  // Fetch users with voice status (all companies for superusers)
  const { data: usersData, isLoading } = useQuery({
    queryKey: ["voice-users"],
    queryFn: async () => {
      const { data } = await voiceService.listUsers(true);
      return data.users;
    },
    enabled: canManageVoice,
  });

  // Grant access mutation
  const grantAccess = useMutation({
    mutationFn: ({ membershipId, quota }: { membershipId: number; quota: number }) =>
      voiceService.grantAccess(membershipId, quota),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["voice-users"] });
      toast({ title: "Voice access granted", variant: "default" });
      setGrantDialogOpen(false);
    },
    onError: (error) => {
      toast({ title: "Failed to grant access", description: getErrorMessage(error), variant: "destructive" });
    },
  });

  // Revoke access mutation
  const revokeAccess = useMutation({
    mutationFn: (membershipId: number) => voiceService.revokeAccess(membershipId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["voice-users"] });
      toast({ title: "Voice access revoked", variant: "default" });
    },
    onError: (error) => {
      toast({ title: "Failed to revoke access", description: getErrorMessage(error), variant: "destructive" });
    },
  });

  // Refill quota mutation
  const refillQuota = useMutation({
    mutationFn: ({ membershipId, options }: { membershipId: number; options: { additional_quota?: number; new_quota?: number; reset_usage?: boolean } }) =>
      voiceService.refillQuota(membershipId, options),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["voice-users"] });
      toast({ title: "Quota updated", variant: "default" });
      setRefillDialogOpen(false);
    },
    onError: (error) => {
      toast({ title: "Failed to update quota", description: getErrorMessage(error), variant: "destructive" });
    },
  });

  // Dialog states
  const [grantDialogOpen, setGrantDialogOpen] = useState(false);
  const [refillDialogOpen, setRefillDialogOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<VoiceUserStatus | null>(null);
  const [grantQuota, setGrantQuota] = useState("50");
  const [additionalQuota, setAdditionalQuota] = useState("25");
  const [resetUsage, setResetUsage] = useState(false);

  const handleGrantAccess = (user: VoiceUserStatus) => {
    setSelectedUser(user);
    setGrantQuota("50");
    setGrantDialogOpen(true);
  };

  const handleRefillQuota = (user: VoiceUserStatus) => {
    setSelectedUser(user);
    setAdditionalQuota("25");
    setResetUsage(false);
    setRefillDialogOpen(true);
  };

  const submitGrantAccess = () => {
    if (!selectedUser) return;
    const quota = parseInt(grantQuota, 10);
    if (isNaN(quota) || quota <= 0) {
      toast({ title: "Invalid quota", description: "Quota must be a positive number", variant: "destructive" });
      return;
    }
    grantAccess.mutate({ membershipId: selectedUser.membership_id, quota });
  };

  const submitRefillQuota = () => {
    if (!selectedUser) return;
    const additional = parseInt(additionalQuota, 10);
    if (isNaN(additional) || additional <= 0) {
      toast({ title: "Invalid quota", description: "Additional quota must be a positive number", variant: "destructive" });
      return;
    }
    refillQuota.mutate({
      membershipId: selectedUser.membership_id,
      options: { additional_quota: additional, reset_usage: resetUsage },
    });
  };

  if (!canManageVoice) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center min-h-[400px]">
          <EmptyState
            icon={<MicOff className="h-12 w-12" />}
            title="Access Denied"
            description="You don't have permission to manage voice settings."
          />
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <PageHeader
        title="Voice Feature Management"
        subtitle="Grant and manage voice input access for users"
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Mic className="h-5 w-5" />
            User Voice Access
          </CardTitle>
          <CardDescription>
            Control which users can use voice input and manage their quotas
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex justify-center py-12">
              <LoadingSpinner />
            </div>
          ) : !usersData || usersData.length === 0 ? (
            <EmptyState
              icon={<Mic className="h-12 w-12" />}
              title="No Users"
              description="No users found."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Company</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Voice Status</TableHead>
                  <TableHead>Usage</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usersData.map((user) => (
                  <TableRow key={user.membership_id}>
                    <TableCell>
                      <div>
                        <div className="font-medium">{user.user_name || user.user_email}</div>
                        <div className="text-sm text-muted-foreground">{user.user_email}</div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <span className="text-sm">{user.company_name}</span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{user.role}</Badge>
                    </TableCell>
                    <TableCell>
                      {user.voice_enabled ? (
                        <Badge variant="default" className="bg-green-600">
                          <Mic className="h-3 w-3 me-1" />
                          Enabled
                        </Badge>
                      ) : (
                        <Badge variant="secondary">
                          <MicOff className="h-3 w-3 me-1" />
                          Disabled
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {user.voice_enabled && user.voice_quota ? (
                        <div className="space-y-1">
                          <div className="flex items-center gap-2 text-sm">
                            <span>{user.voice_rows_used ?? 0}</span>
                            <span className="text-muted-foreground">/</span>
                            <span>{user.voice_quota}</span>
                            <span className="text-muted-foreground">
                              ({user.voice_remaining ?? 0} remaining)
                            </span>
                          </div>
                          <Progress
                            value={((user.voice_rows_used ?? 0) / user.voice_quota) * 100}
                            className="h-2 w-32"
                          />
                        </div>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {user.voice_enabled ? (
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleRefillQuota(user)}
                          >
                            <RefreshCw className="h-4 w-4 me-1" />
                            Refill
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => revokeAccess.mutate(user.membership_id)}
                            disabled={revokeAccess.isPending}
                          >
                            <MicOff className="h-4 w-4 me-1" />
                            Revoke
                          </Button>
                        </div>
                      ) : (
                        <Button
                          variant="default"
                          size="sm"
                          onClick={() => handleGrantAccess(user)}
                        >
                          <Plus className="h-4 w-4 me-1" />
                          Grant Access
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Grant Access Dialog */}
      <Dialog open={grantDialogOpen} onOpenChange={setGrantDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Grant Voice Access</DialogTitle>
            <DialogDescription>
              Grant voice input access to {selectedUser?.user_name || selectedUser?.user_email}
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <Label htmlFor="quota">Initial Quota</Label>
            <Input
              id="quota"
              type="number"
              min="1"
              value={grantQuota}
              onChange={(e) => setGrantQuota(e.target.value)}
              placeholder="Number of voice entries"
              className="mt-2"
            />
            <p className="text-sm text-muted-foreground mt-2">
              The user will be able to use voice input this many times before needing a refill.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setGrantDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submitGrantAccess} disabled={grantAccess.isPending}>
              {grantAccess.isPending ? "Granting..." : "Grant Access"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Refill Quota Dialog */}
      <Dialog open={refillDialogOpen} onOpenChange={setRefillDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Refill Voice Quota</DialogTitle>
            <DialogDescription>
              Add quota for {selectedUser?.user_name || selectedUser?.user_email}
            </DialogDescription>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <div>
              <Label htmlFor="additional">Additional Quota</Label>
              <Input
                id="additional"
                type="number"
                min="1"
                value={additionalQuota}
                onChange={(e) => setAdditionalQuota(e.target.value)}
                placeholder="Amount to add"
                className="mt-2"
              />
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="reset"
                checked={resetUsage}
                onChange={(e) => setResetUsage(e.target.checked)}
                className="rounded"
              />
              <Label htmlFor="reset" className="font-normal">
                Reset usage counter to 0 (full refill)
              </Label>
            </div>
            {selectedUser && (
              <div className="bg-muted p-3 rounded-md text-sm">
                <p>Current: {selectedUser.voice_rows_used ?? 0} / {selectedUser.voice_quota ?? 0}</p>
                <p>Remaining: {selectedUser.voice_remaining ?? 0}</p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRefillDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submitRefillQuota} disabled={refillQuota.isPending}>
              {refillQuota.isPending ? "Updating..." : "Update Quota"}
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
