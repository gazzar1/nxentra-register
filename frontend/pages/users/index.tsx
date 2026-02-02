import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { Plus, MoreHorizontal } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  useCreateUser,
  useUpdateRole,
  useUserPermissions,
  usePermissions,
  useGrantPermission,
  useRevokePermission,
} from "@/queries/useUsers";
import { useAuth } from "@/contexts/AuthContext";
import type { UserRole } from "@/types/user";

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
  const { user: currentUser, hasPermission } = useAuth();
  const { data: members, isLoading } = useUsers() as { data: MemberFlat[] | undefined; isLoading: boolean };

  const canManageUsers = hasPermission("users.manage");

  // Add User dialog state
  const [addOpen, setAddOpen] = useState(false);
  const [addForm, setAddForm] = useState({ email: "", name: "", password: "", role: "USER" as UserRole });
  const createUser = useCreateUser();

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

  const handleAddUser = async () => {
    if (!addForm.email || !addForm.password) return;
    try {
      await createUser.mutateAsync({
        email: addForm.email,
        name: addForm.name,
        password: addForm.password,
        role: addForm.role,
      });
      setAddOpen(false);
      setAddForm({ email: "", name: "", password: "", role: "USER" });
    } catch {
      // error handled by react-query
    }
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

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("settings:users.title")}
          subtitle={t("settings:users.subtitle")}
          actions={
            canManageUsers && (
              <Button onClick={() => setAddOpen(true)}>
                <Plus className="me-2 h-4 w-4" />
                {t("settings:users.addUser")}
              </Button>
            )
          }
        />

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
      </div>

      {/* Add User Dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("settings:users.addUser")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="add-email">{t("settings:users.email")}</Label>
              <Input
                id="add-email"
                type="email"
                value={addForm.email}
                onChange={(e) => setAddForm((f) => ({ ...f, email: e.target.value }))}
                placeholder="user@example.com"
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="add-name">{t("settings:users.name")}</Label>
              <Input
                id="add-name"
                value={addForm.name}
                onChange={(e) => setAddForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="add-password">Password</Label>
              <Input
                id="add-password"
                type="password"
                value={addForm.password}
                onChange={(e) => setAddForm((f) => ({ ...f, password: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("settings:users.role")}</Label>
              <Select
                value={addForm.role}
                onValueChange={(v) => setAddForm((f) => ({ ...f, role: v as UserRole }))}
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
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              {t("actions.cancel")}
            </Button>
            <Button
              onClick={handleAddUser}
              disabled={!addForm.email || !addForm.password || createUser.isPending}
            >
              {createUser.isPending ? t("actions.loading") : t("actions.create")}
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
