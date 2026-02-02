import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { Plus, Pencil, Trash2, Settings2, ArrowRight } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, LoadingSpinner, ConfirmDialog } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useToast } from "@/components/ui/toaster";
import {
  useSourceSystems,
  useCreateSourceSystem,
  useUpdateSourceSystem,
  useDeactivateSourceSystem,
} from "@/queries/useEdim";
import type {
  SourceSystem,
  SourceSystemCreatePayload,
  SourceSystemType,
  TrustLevel,
  SOURCE_SYSTEM_TYPE_LABELS,
  TRUST_LEVEL_LABELS,
} from "@/types/edim";

const SYSTEM_TYPE_OPTIONS: { value: SourceSystemType; label: string }[] = [
  { value: "POS", label: "Point of Sale" },
  { value: "HR", label: "Human Resources" },
  { value: "INVENTORY", label: "Inventory Management" },
  { value: "PAYROLL", label: "Payroll" },
  { value: "BANK", label: "Bank Feed" },
  { value: "ERP", label: "External ERP" },
  { value: "CUSTOM", label: "Custom" },
];

const TRUST_LEVEL_OPTIONS: { value: TrustLevel; label: string }[] = [
  { value: "INFORMATIONAL", label: "Informational (no auto-post)" },
  { value: "OPERATIONAL", label: "Operational (auto-draft)" },
  { value: "FINANCIAL", label: "Financial (auto-post eligible)" },
];

export default function IntegrationsPage() {
  const { t } = useTranslation(["common", "settings", "edim"]);
  const { toast } = useToast();
  const { hasPermission } = useAuth();

  const { data: sourceSystems, isLoading, refetch } = useSourceSystems();
  const createMutation = useCreateSourceSystem();
  const updateMutation = useUpdateSourceSystem();
  const deactivateMutation = useDeactivateSourceSystem();

  // Dialog state
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingSystem, setEditingSystem] = useState<SourceSystem | null>(null);
  const [form, setForm] = useState<SourceSystemCreatePayload>({
    code: "",
    name: "",
    system_type: "CUSTOM",
    trust_level: "INFORMATIONAL",
    description: "",
  });

  // Delete confirm
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SourceSystem | null>(null);

  const canManage = hasPermission("edim.manage_sources");

  const openCreateDialog = () => {
    setEditingSystem(null);
    setForm({
      code: "",
      name: "",
      system_type: "CUSTOM",
      trust_level: "INFORMATIONAL",
      description: "",
    });
    setDialogOpen(true);
  };

  const openEditDialog = (system: SourceSystem) => {
    setEditingSystem(system);
    setForm({
      code: system.code,
      name: system.name,
      system_type: system.system_type,
      trust_level: system.trust_level,
      description: system.description,
    });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.code || !form.name) return;

    try {
      if (editingSystem) {
        await updateMutation.mutateAsync({
          id: editingSystem.id,
          data: {
            name: form.name,
            system_type: form.system_type,
            trust_level: form.trust_level,
            description: form.description,
          },
        });
        toast({
          title: t("messages.success"),
          description: t("edim:sourceSystems.updateSuccess", "Source system updated"),
        });
      } else {
        await createMutation.mutateAsync(form);
        toast({
          title: t("messages.success"),
          description: t("edim:sourceSystems.createSuccess", "Source system created"),
        });
      }
      setDialogOpen(false);
      refetch();
    } catch (error: any) {
      toast({
        title: t("messages.error"),
        description: error?.response?.data?.detail || t("edim:sourceSystems.saveError", "Failed to save"),
        variant: "destructive",
      });
    }
  };

  const handleDeactivate = async () => {
    if (!deleteTarget) return;

    try {
      await deactivateMutation.mutateAsync(deleteTarget.id);
      toast({
        title: t("messages.success"),
        description: t("edim:sourceSystems.deactivateSuccess", "Source system deactivated"),
      });
      setDeleteConfirmOpen(false);
      refetch();
    } catch (error: any) {
      toast({
        title: t("messages.error"),
        description: error?.response?.data?.detail || t("edim:sourceSystems.deactivateError", "Failed to deactivate"),
        variant: "destructive",
      });
    }
  };

  const getSystemTypeLabel = (type: SourceSystemType) => {
    return SYSTEM_TYPE_OPTIONS.find((o) => o.value === type)?.label || type;
  };

  const getTrustLevelVariant = (level: TrustLevel) => {
    switch (level) {
      case "FINANCIAL":
        return "default";
      case "OPERATIONAL":
        return "secondary";
      default:
        return "outline";
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <div className="flex h-64 items-center justify-center">
          <LoadingSpinner />
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("edim:integrations.title", "Integrations")}
          subtitle={t("edim:integrations.subtitle", "Manage external data sources and import configurations")}
          actions={
            canManage && (
              <Button onClick={openCreateDialog}>
                <Plus className="me-2 h-4 w-4" />
                {t("edim:sourceSystems.create", "Add Source System")}
              </Button>
            )
          }
        />

        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("edim:sourceSystems.code", "Code")}</TableHead>
                <TableHead>{t("edim:sourceSystems.name", "Name")}</TableHead>
                <TableHead>{t("edim:sourceSystems.type", "Type")}</TableHead>
                <TableHead>{t("edim:sourceSystems.trustLevel", "Trust Level")}</TableHead>
                <TableHead>{t("edim:sourceSystems.status", "Status")}</TableHead>
                <TableHead className="w-32">{t("actions.actions", "Actions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!sourceSystems || sourceSystems.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                    {t("edim:sourceSystems.empty", "No source systems configured yet")}
                  </TableCell>
                </TableRow>
              ) : (
                sourceSystems.map((system) => (
                  <TableRow key={system.id}>
                    <TableCell className="font-mono text-sm">{system.code}</TableCell>
                    <TableCell className="font-medium">{system.name}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{getSystemTypeLabel(system.system_type)}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={getTrustLevelVariant(system.trust_level)}>
                        {TRUST_LEVEL_OPTIONS.find((o) => o.value === system.trust_level)?.label?.split(" ")[0] || system.trust_level}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={system.is_active ? "default" : "secondary"}>
                        {system.is_active ? t("status.active", "Active") : t("status.inactive", "Inactive")}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <Link href={`/settings/integrations/${system.id}`}>
                          <Button variant="ghost" size="icon" title={t("edim:sourceSystems.configure", "Configure")}>
                            <Settings2 className="h-4 w-4" />
                          </Button>
                        </Link>
                        {canManage && (
                          <>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => openEditDialog(system)}
                              title={t("actions.edit", "Edit")}
                            >
                              <Pencil className="h-4 w-4" />
                            </Button>
                            {system.is_active && (
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => {
                                  setDeleteTarget(system);
                                  setDeleteConfirmOpen(true);
                                }}
                                title={t("edim:sourceSystems.deactivate", "Deactivate")}
                              >
                                <Trash2 className="h-4 w-4 text-destructive" />
                              </Button>
                            )}
                          </>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        {/* Create/Edit Dialog */}
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>
                {editingSystem
                  ? t("edim:sourceSystems.edit", "Edit Source System")
                  : t("edim:sourceSystems.create", "Add Source System")}
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("edim:sourceSystems.code", "Code")} *</Label>
                  <Input
                    value={form.code}
                    onChange={(e) => setForm({ ...form, code: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })}
                    placeholder="shopify_pos"
                    disabled={!!editingSystem}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t("edim:sourceSystems.name", "Name")} *</Label>
                  <Input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="Shopify POS"
                  />
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("edim:sourceSystems.type", "System Type")}</Label>
                  <Select
                    value={form.system_type}
                    onValueChange={(value) => setForm({ ...form, system_type: value as SourceSystemType })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SYSTEM_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>{t("edim:sourceSystems.trustLevel", "Trust Level")}</Label>
                  <Select
                    value={form.trust_level}
                    onValueChange={(value) => setForm({ ...form, trust_level: value as TrustLevel })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TRUST_LEVEL_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="space-y-2">
                <Label>{t("edim:sourceSystems.description", "Description")}</Label>
                <Textarea
                  value={form.description || ""}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder={t("edim:sourceSystems.descriptionPlaceholder", "Optional description...")}
                  rows={3}
                />
              </div>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setDialogOpen(false)}
                disabled={createMutation.isPending || updateMutation.isPending}
              >
                {t("actions.cancel")}
              </Button>
              <Button
                onClick={handleSave}
                disabled={createMutation.isPending || updateMutation.isPending || !form.code || !form.name}
              >
                {createMutation.isPending || updateMutation.isPending ? t("actions.loading") : t("actions.save")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Deactivate Confirm Dialog */}
        <ConfirmDialog
          open={deleteConfirmOpen}
          onOpenChange={setDeleteConfirmOpen}
          title={t("edim:sourceSystems.deactivateTitle", "Deactivate Source System")}
          description={t(
            "edim:sourceSystems.deactivateDescription",
            'Are you sure you want to deactivate "{{name}}"? This will prevent new imports from this source.',
            { name: deleteTarget?.name || "" }
          )}
          variant="destructive"
          onConfirm={handleDeactivate}
          isLoading={deactivateMutation.isPending}
        />
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale || "en", ["common", "settings", "edim"])),
  },
});
