import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { Plus, MoreHorizontal, Mail, Clock, XCircle, RefreshCw, UserPlus } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import {
  useUsers,
  useUpdateRole,
  useUserPermissions,
  usePermissions,
  useGrantPermission,
  useRevokePermission,
} from "@/queries/useUsers";
import {
  useInvitations,
  useCreateInvitation,
  useCancelInvitation,
  useResendInvitation,
} from "@/queries/useInvitations";
import { companyService } from "@/services/company.service";
import { useAuth } from "@/contexts/AuthContext";
import type { UserRole, Company } from "@/types/user";

interface MemberFlat {
  id: number;
  public_id: string;
  email: string;
  name: string;
  role: UserRole;
  membership_id: number;
  membership_public_id: string;
  is_active: boolean;
  joined_at: string;
}

export default function UsersPage() {
  const { t } = useTranslation(["common", "settings"]);
  const { user: currentUser, company: currentCompany, hasPermission } = useAuth();
  const { data: members, isLoading } = useUsers() as { data: MemberFlat[] | undefined; isLoading: boolean };
  const { data: invitationsData, isLoading: invitationsLoading } = useInvitations();
  const { data: allPerms } = usePermissions();

  // Fetch companies the user has access to
  const { data: companies } = useQuery({
    queryKey: ['companies'],
    queryFn: async () => {
      const { data } = await companyService.list();
      return data;
    },
  });

  const canManageUsers = hasPermission("users.manage");

  // Invite User dialog state
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteForm, setInviteForm] = useState({
    email: "",
    name: "",
    role: "USER" as UserRole,
    company_ids: [] as number[],
    permission_codes: [] as string[],
  });
  const createInvitation = useCreateInvitation();
  const cancelInvitation = useCancelInvitation();
  const resendInvitation = useResendInvitation();

  // Edit Role dialog state
  const [editOpen, setEditOpen] = useState(false);
  const [editMember, setEditMember] = useState<MemberFlat | null>(null);
  const [editRole, setEditRole] = useState<UserRole>("USER");
  const updateRole = useUpdateRole();

  // Permissions dialog state
  const [permOpen, setPermOpen] = useState(false);
  const [permMember, setPermMember] = useState<MemberFlat | null>(null);

  const getRoleBadgeVariant = (role: string) => {
    switch (role) {
      case "OWNER":
        return "default";
      case "ADMIN":
        return "secondary";
      default:
        return "outline";
    }
  };

  const resetInviteForm = () => {
    setInviteForm({
      email: "",
      name: "",
      role: "USER",
      company_ids: currentCompany ? [currentCompany.id] : [],
      permission_codes: [],
    });
    setInviteError(null);
  };

  const openInviteDialog = () => {
    resetInviteForm();
    setInviteOpen(true);
  };

  const handleInviteUser = async () => {
    if (!inviteForm.email) return;
    setInviteError(null);
    try {
      await createInvitation.mutateAsync({
        email: inviteForm.email,
        name: inviteForm.name,
        role: inviteForm.role,
        company_ids: inviteForm.company_ids.length > 0 ? inviteForm.company_ids : undefined,
        permission_codes: inviteForm.permission_codes.length > 0 ? inviteForm.permission_codes : undefined,
      });
      setInviteOpen(false);
      resetInviteForm();
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      setInviteError(error?.response?.data?.detail || "Failed to send invitation. Please try again.");
    }
  };

  const toggleCompany = (companyId: number) => {
    setInviteForm((f) => ({
      ...f,
      company_ids: f.company_ids.includes(companyId)
        ? f.company_ids.filter((id) => id !== companyId)
        : [...f.company_ids, companyId],
    }));
  };

  const togglePermission = (code: string) => {
    setInviteForm((f) => ({
      ...f,
      permission_codes: f.permission_codes.includes(code)
        ? f.permission_codes.filter((c) => c !== code)
        : [...f.permission_codes, code],
    }));
  };

  const handleEditRole = async () => {
    if (!editMember) return;
    try {
      await updateRole.mutateAsync({
        membershipId: editMember.membership_id,
        data: { role: editRole },
      });
      setEditOpen(false);
      setEditMember(null);
    } catch {
      // error handled by react-query
    }
  };

  const openEdit = (member: MemberFlat) => {
    setEditMember(member);
    setEditRole(member.role);
    setEditOpen(true);
  };

  const openPermissions = (member: MemberFlat) => {
    setPermMember(member);
    setPermOpen(true);
  };

  const handleCancelInvitation = async (id: number) => {
    try {
      await cancelInvitation.mutateAsync({ id });
    } catch {
      // error handled by react-query
    }
  };

  const handleResendInvitation = async (id: number) => {
    try {
      await resendInvitation.mutateAsync(id);
    } catch {
      // error handled by react-query
    }
  };

  // Group permissions by category for the invite form
  const groupedPerms: Record<string, { code: string; name: string }[]> = {};
  (allPerms || []).forEach((p) => {
    const category = p.code.split(".")[0];
    if (!groupedPerms[category]) groupedPerms[category] = [];
    groupedPerms[category].push(p);
  });

  const pendingInvitations = invitationsData?.invitations || [];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("settings:users.title")}
          subtitle={t("settings:users.subtitle")}
          actions={
            canManageUsers && (
              <Button onClick={openInviteDialog}>
                <UserPlus className="me-2 h-4 w-4" />
                Invite User
              </Button>
            )
          }
        />

        <Tabs defaultValue="members" className="space-y-4">
          <TabsList>
            <TabsTrigger value="members">Members</TabsTrigger>
            {canManageUsers && (
              <TabsTrigger value="invitations">
                Pending Invitations
                {pendingInvitations.length > 0 && (
                  <Badge variant="secondary" className="ms-2">
                    {pendingInvitations.length}
                  </Badge>
                )}
              </TabsTrigger>
            )}
          </TabsList>

          <TabsContent value="members">
            <Card>
              <CardContent className="pt-6">
                {isLoading ? (
                  <div className="flex justify-center py-12">
                    <LoadingSpinner size="lg" />
                  </div>
                ) : !members || members.length === 0 ? (
                  <EmptyState title={t("messages.noData")} />
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t("settings:users.name")}</TableHead>
                        <TableHead>{t("settings:users.email")}</TableHead>
                        <TableHead>{t("settings:users.role")}</TableHead>
                        <TableHead>{t("settings:users.status")}</TableHead>
                        {canManageUsers && <TableHead className="w-12"></TableHead>}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {members.map((member) => (
                        <TableRow key={member.id}>
                          <TableCell className="font-medium">
                            {member.name || member.email || "—"}
                            {member.id === currentUser?.id && (
                              <Badge variant="outline" className="ms-2">
                                You
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell>{member.email || "—"}</TableCell>
                          <TableCell>
                            <Badge variant={getRoleBadgeVariant(member.role)}>
                              {t(`settings:roles.${member.role}`, member.role)}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Badge variant={member.is_active ? "default" : "secondary"}>
                              {member.is_active ? t("status.active") : t("status.inactive")}
                            </Badge>
                          </TableCell>
                          {canManageUsers && (
                            <TableCell>
                              <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                  <Button variant="ghost" size="icon">
                                    <MoreHorizontal className="h-4 w-4" />
                                  </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                  <DropdownMenuItem onClick={() => openEdit(member)}>
                                    {t("settings:users.editUser")}
                                  </DropdownMenuItem>
                                  <DropdownMenuItem onClick={() => openPermissions(member)}>
                                    {t("settings:permissions.title")}
                                  </DropdownMenuItem>
                                  {member.id !== currentUser?.id && (
                                    <DropdownMenuItem className="text-destructive">
                                      {t("settings:users.deleteUser")}
                                    </DropdownMenuItem>
                                  )}
                                </DropdownMenuContent>
                              </DropdownMenu>
                            </TableCell>
                          )}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="invitations">
            <Card>
              <CardContent className="pt-6">
                {invitationsLoading ? (
                  <div className="flex justify-center py-12">
                    <LoadingSpinner size="lg" />
                  </div>
                ) : pendingInvitations.length === 0 ? (
                  <EmptyState
                    title="No pending invitations"
                    description="Invite users to join your company"
                  />
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Email</TableHead>
                        <TableHead>Name</TableHead>
                        <TableHead>Role</TableHead>
                        <TableHead>Invited By</TableHead>
                        <TableHead>Expires</TableHead>
                        <TableHead className="w-12"></TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {pendingInvitations.map((invitation) => (
                        <TableRow key={invitation.id}>
                          <TableCell className="font-medium">
                            <div className="flex items-center gap-2">
                              <Mail className="h-4 w-4 text-muted-foreground" />
                              {invitation.email}
                            </div>
                          </TableCell>
                          <TableCell>{invitation.name || "—"}</TableCell>
                          <TableCell>
                            <Badge variant={getRoleBadgeVariant(invitation.role)}>
                              {t(`settings:roles.${invitation.role}`, invitation.role)}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            {invitation.invited_by_name || invitation.invited_by_email || "—"}
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-1 text-sm text-muted-foreground">
                              <Clock className="h-3 w-3" />
                              {new Date(invitation.expires_at).toLocaleDateString()}
                            </div>
                          </TableCell>
                          <TableCell>
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button variant="ghost" size="icon">
                                  <MoreHorizontal className="h-4 w-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <DropdownMenuItem onClick={() => handleResendInvitation(invitation.id)}>
                                  <RefreshCw className="me-2 h-4 w-4" />
                                  Resend
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={() => handleCancelInvitation(invitation.id)}
                                  className="text-destructive"
                                >
                                  <XCircle className="me-2 h-4 w-4" />
                                  Cancel
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>

      {/* Invite User Dialog */}
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Invite User</DialogTitle>
            <DialogDescription>
              Send an invitation email. The user will create their own password when they accept.
            </DialogDescription>
          </DialogHeader>
          {inviteError && (
            <Alert variant="destructive" className="mt-4">
              <AlertDescription>{inviteError}</AlertDescription>
            </Alert>
          )}
          <div className="space-y-6 py-4">
            {/* Basic Info */}
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="invite-email">Email *</Label>
                <Input
                  id="invite-email"
                  type="email"
                  value={inviteForm.email}
                  onChange={(e) => setInviteForm((f) => ({ ...f, email: e.target.value }))}
                  placeholder="user@example.com"
                  autoFocus
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="invite-name">Name</Label>
                <Input
                  id="invite-name"
                  value={inviteForm.name}
                  onChange={(e) => setInviteForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="John Doe"
                />
              </div>
            </div>

            {/* Role */}
            <div className="space-y-2">
              <Label>Role</Label>
              <Select
                value={inviteForm.role}
                onValueChange={(v) => setInviteForm((f) => ({ ...f, role: v as UserRole }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ADMIN">{t("settings:roles.ADMIN")}</SelectItem>
                  <SelectItem value="USER">{t("settings:roles.USER")}</SelectItem>
                  <SelectItem value="VIEWER">{t("settings:roles.VIEWER")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Companies */}
            {companies && companies.length > 1 && (
              <div className="space-y-3">
                <Label>Company Access</Label>
                <p className="text-sm text-muted-foreground">
                  Select which companies this user can access
                </p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {companies.map((company) => (
                    <label
                      key={company.id}
                      className="flex items-center gap-3 rounded-md border p-3 cursor-pointer hover:bg-muted/50"
                    >
                      <Checkbox
                        checked={inviteForm.company_ids.includes(company.id)}
                        onCheckedChange={() => toggleCompany(company.id)}
                      />
                      <div>
                        <div className="font-medium">{company.name}</div>
                        {company.id === currentCompany?.id && (
                          <div className="text-xs text-muted-foreground">Current company</div>
                        )}
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* Permissions */}
            <div className="space-y-3">
              <Label>Permissions (Optional)</Label>
              <p className="text-sm text-muted-foreground">
                Grant specific permissions to this user
              </p>
              <div className="max-h-48 overflow-y-auto border rounded-md p-3 space-y-4">
                {Object.entries(groupedPerms).map(([category, perms]) => (
                  <div key={category}>
                    <h4 className="text-sm font-semibold text-muted-foreground mb-2 capitalize">
                      {t(`settings:permissions.categories.${category}`, category)}
                    </h4>
                    <div className="grid gap-1 sm:grid-cols-2">
                      {perms.map((p) => (
                        <label
                          key={p.code}
                          className="flex items-center gap-2 rounded px-2 py-1 hover:bg-muted cursor-pointer"
                        >
                          <Checkbox
                            checked={inviteForm.permission_codes.includes(p.code)}
                            onCheckedChange={() => togglePermission(p.code)}
                          />
                          <span className="text-sm">{p.name || p.code}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
                {Object.keys(groupedPerms).length === 0 && (
                  <p className="text-sm text-muted-foreground text-center py-2">
                    No permissions available
                  </p>
                )}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setInviteOpen(false)}>
              {t("actions.cancel")}
            </Button>
            <Button
              onClick={handleInviteUser}
              disabled={!inviteForm.email || createInvitation.isPending}
            >
              {createInvitation.isPending ? t("actions.loading") : "Send Invitation"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Role Dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("settings:users.editUser")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>{t("settings:users.email")}</Label>
              <Input value={editMember?.email || ""} disabled />
            </div>
            <div className="space-y-2">
              <Label>{t("settings:users.role")}</Label>
              <Select value={editRole} onValueChange={(v) => setEditRole(v as UserRole)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="OWNER">{t("settings:roles.OWNER")}</SelectItem>
                  <SelectItem value="ADMIN">{t("settings:roles.ADMIN")}</SelectItem>
                  <SelectItem value="USER">{t("settings:roles.USER")}</SelectItem>
                  <SelectItem value="VIEWER">{t("settings:roles.VIEWER")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)}>
              {t("actions.cancel")}
            </Button>
            <Button onClick={handleEditRole} disabled={updateRole.isPending}>
              {updateRole.isPending ? t("actions.loading") : t("actions.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Permissions Dialog */}
      <Dialog open={permOpen} onOpenChange={setPermOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {t("settings:permissions.title")} — {permMember?.name || permMember?.email}
            </DialogTitle>
          </DialogHeader>
          {permMember && <PermissionsPanel membershipId={permMember.membership_id} />}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPermOpen(false)}>
              {t("actions.close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}

function PermissionsPanel({ membershipId }: { membershipId: number }) {
  const { t } = useTranslation(["settings"]);
  const { data: userPerms, isLoading: permsLoading } = useUserPermissions(membershipId);
  const { data: allPerms, isLoading: allLoading } = usePermissions();
  const grantPerm = useGrantPermission();
  const revokePerm = useRevokePermission();

  if (permsLoading || allLoading) {
    return <div className="flex justify-center py-8"><LoadingSpinner /></div>;
  }

  // userPerms may be {permissions: [{code, name, module}, ...]} or string[]
  const permCodes: string[] = Array.isArray(userPerms)
    ? userPerms
    : (userPerms as any)?.permissions?.map((p: any) => p.code) || [];
  const permSet = new Set(permCodes);

  // Group permissions by category
  const grouped: Record<string, { code: string; name: string }[]> = {};
  (allPerms || []).forEach((p) => {
    const category = p.code.split(".")[0];
    if (!grouped[category]) grouped[category] = [];
    grouped[category].push(p);
  });

  const toggle = async (code: string) => {
    if (permSet.has(code)) {
      await revokePerm.mutateAsync({ membershipId, permissionCode: code });
    } else {
      await grantPerm.mutateAsync({ membershipId, permissionCode: code });
    }
  };

  return (
    <div className="space-y-4 py-2 max-h-96 overflow-y-auto">
      {Object.entries(grouped).map(([category, perms]) => (
        <div key={category}>
          <h4 className="text-sm font-semibold text-muted-foreground mb-2 capitalize">
            {t(`settings:permissions.categories.${category}`, category)}
          </h4>
          <div className="space-y-1">
            {perms.map((p) => (
              <label
                key={p.code}
                className="flex items-center gap-3 rounded px-2 py-1.5 hover:bg-muted cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={permSet.has(p.code)}
                  onChange={() => toggle(p.code)}
                  className="h-4 w-4 rounded border-border"
                />
                <span className="text-sm">{p.name || p.code}</span>
              </label>
            ))}
          </div>
        </div>
      ))}
      {Object.keys(grouped).length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-4">
          No permissions available
        </p>
      )}
    </div>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "settings"])),
    },
  };
};
