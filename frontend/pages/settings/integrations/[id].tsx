import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import {
  Plus,
  Pencil,
  Trash2,
  ArrowLeft,
  Check,
  X,
  Play,
  Archive,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
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
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader, LoadingSpinner, ConfirmDialog } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useToast } from "@/components/ui/toaster";
import {
  useSourceSystem,
  useMappingProfiles,
  useCreateMappingProfile,
  useActivateMappingProfile,
  useDeprecateMappingProfile,
  useCrosswalks,
  useCreateCrosswalk,
  useUpdateCrosswalk,
  useVerifyCrosswalk,
  useRejectCrosswalk,
} from "@/queries/useEdim";
import { useAccounts } from "@/queries/useAccounts";
import type {
  MappingProfile,
  MappingProfileCreatePayload,
  DocumentType,
  PostingPolicy,
  IdentityCrosswalk,
  CrosswalkCreatePayload,
  CrosswalkObjectType,
} from "@/types/edim";

const DOCUMENT_TYPE_OPTIONS: { value: DocumentType; label: string }[] = [
  { value: "SALES", label: "Sales" },
  { value: "PAYROLL", label: "Payroll" },
  { value: "INVENTORY_MOVE", label: "Inventory Movement" },
  { value: "JOURNAL", label: "Generic Journal" },
  { value: "BANK_TRANSACTION", label: "Bank Transaction" },
  { value: "CUSTOM", label: "Custom" },
];

const POSTING_POLICY_OPTIONS: { value: PostingPolicy; label: string }[] = [
  { value: "AUTO_DRAFT", label: "Auto Draft" },
  { value: "AUTO_POST", label: "Auto Post" },
  { value: "MANUAL_APPROVAL", label: "Manual Approval" },
];

const OBJECT_TYPE_OPTIONS: { value: CrosswalkObjectType; label: string }[] = [
  { value: "ACCOUNT", label: "Account" },
  { value: "CUSTOMER", label: "Customer" },
  { value: "ITEM", label: "Item" },
  { value: "TAX_CODE", label: "Tax Code" },
  { value: "DIMENSION", label: "Dimension" },
  { value: "DIMENSION_VALUE", label: "Dimension Value" },
];

export default function SourceSystemDetailPage() {
  const { t } = useTranslation(["common", "settings", "edim"]);
  const { toast } = useToast();
  const { hasPermission } = useAuth();
  const router = useRouter();
  const { id } = router.query;
  const sourceSystemId = parseInt(id as string);

  // Queries
  const { data: sourceSystem, isLoading: loadingSystem } = useSourceSystem(sourceSystemId);
  const { data: mappingProfiles, isLoading: loadingProfiles, refetch: refetchProfiles } = useMappingProfiles({
    source_system: sourceSystemId,
  });
  const { data: crosswalks, isLoading: loadingCrosswalks, refetch: refetchCrosswalks } = useCrosswalks({
    source_system: sourceSystemId,
  });
  const { data: accounts } = useAccounts();

  // Mutations
  const createProfileMutation = useCreateMappingProfile();
  const activateProfileMutation = useActivateMappingProfile();
  const deprecateProfileMutation = useDeprecateMappingProfile();
  const createCrosswalkMutation = useCreateCrosswalk();
  const updateCrosswalkMutation = useUpdateCrosswalk();
  const verifyCrosswalkMutation = useVerifyCrosswalk();
  const rejectCrosswalkMutation = useRejectCrosswalk();

  // Profile dialog state
  const [profileDialogOpen, setProfileDialogOpen] = useState(false);
  const [profileForm, setProfileForm] = useState<MappingProfileCreatePayload>({
    source_system_id: sourceSystemId,
    name: "",
    document_type: "JOURNAL",
    posting_policy: "MANUAL_APPROVAL",
    field_mappings: [],
  });

  // Crosswalk dialog state
  const [crosswalkDialogOpen, setCrosswalkDialogOpen] = useState(false);
  const [editingCrosswalk, setEditingCrosswalk] = useState<IdentityCrosswalk | null>(null);
  const [crosswalkForm, setCrosswalkForm] = useState<CrosswalkCreatePayload>({
    source_system_id: sourceSystemId,
    object_type: "ACCOUNT",
    external_id: "",
    external_label: "",
    nxentra_id: "",
    nxentra_label: "",
  });

  const canManageMappings = hasPermission("edim.manage_mappings");
  const canManageCrosswalks = hasPermission("edim.manage_crosswalks");

  // Profile handlers
  const openCreateProfileDialog = () => {
    setProfileForm({
      source_system_id: sourceSystemId,
      name: "",
      document_type: "JOURNAL",
      posting_policy: "MANUAL_APPROVAL",
      field_mappings: [],
    });
    setProfileDialogOpen(true);
  };

  const handleSaveProfile = async () => {
    if (!profileForm.name) return;

    try {
      await createProfileMutation.mutateAsync(profileForm);
      toast({
        title: t("messages.success"),
        description: t("edim:mappingProfiles.createSuccess", "Mapping profile created"),
      });
      setProfileDialogOpen(false);
      refetchProfiles();
    } catch (error: any) {
      toast({
        title: t("messages.error"),
        description: error?.response?.data?.detail || t("edim:mappingProfiles.saveError", "Failed to save"),
        variant: "destructive",
      });
    }
  };

  const handleActivateProfile = async (profileId: number) => {
    try {
      await activateProfileMutation.mutateAsync(profileId);
      toast({
        title: t("messages.success"),
        description: t("edim:mappingProfiles.activateSuccess", "Profile activated"),
      });
      refetchProfiles();
    } catch (error: any) {
      toast({
        title: t("messages.error"),
        description: error?.response?.data?.detail || t("edim:mappingProfiles.activateError", "Failed to activate"),
        variant: "destructive",
      });
    }
  };

  const handleDeprecateProfile = async (profileId: number) => {
    try {
      await deprecateProfileMutation.mutateAsync(profileId);
      toast({
        title: t("messages.success"),
        description: t("edim:mappingProfiles.deprecateSuccess", "Profile deprecated"),
      });
      refetchProfiles();
    } catch (error: any) {
      toast({
        title: t("messages.error"),
        description: error?.response?.data?.detail || t("edim:mappingProfiles.deprecateError", "Failed to deprecate"),
        variant: "destructive",
      });
    }
  };

  // Crosswalk handlers
  const openCreateCrosswalkDialog = () => {
    setEditingCrosswalk(null);
    setCrosswalkForm({
      source_system_id: sourceSystemId,
      object_type: "ACCOUNT",
      external_id: "",
      external_label: "",
      nxentra_id: "",
      nxentra_label: "",
    });
    setCrosswalkDialogOpen(true);
  };

  const openEditCrosswalkDialog = (crosswalk: IdentityCrosswalk) => {
    setEditingCrosswalk(crosswalk);
    setCrosswalkForm({
      source_system_id: sourceSystemId,
      object_type: crosswalk.object_type,
      external_id: crosswalk.external_id,
      external_label: crosswalk.external_label,
      nxentra_id: crosswalk.nxentra_id,
      nxentra_label: crosswalk.nxentra_label,
    });
    setCrosswalkDialogOpen(true);
  };

  const handleSaveCrosswalk = async () => {
    if (!crosswalkForm.external_id) return;

    try {
      if (editingCrosswalk) {
        await updateCrosswalkMutation.mutateAsync({
          id: editingCrosswalk.id,
          data: {
            nxentra_id: crosswalkForm.nxentra_id,
            nxentra_label: crosswalkForm.nxentra_label,
            external_label: crosswalkForm.external_label,
          },
        });
        toast({
          title: t("messages.success"),
          description: t("edim:crosswalks.updateSuccess", "Crosswalk updated"),
        });
      } else {
        await createCrosswalkMutation.mutateAsync(crosswalkForm);
        toast({
          title: t("messages.success"),
          description: t("edim:crosswalks.createSuccess", "Crosswalk created"),
        });
      }
      setCrosswalkDialogOpen(false);
      refetchCrosswalks();
    } catch (error: any) {
      toast({
        title: t("messages.error"),
        description: error?.response?.data?.detail || t("edim:crosswalks.saveError", "Failed to save"),
        variant: "destructive",
      });
    }
  };

  const handleVerifyCrosswalk = async (crosswalkId: number) => {
    try {
      await verifyCrosswalkMutation.mutateAsync(crosswalkId);
      toast({
        title: t("messages.success"),
        description: t("edim:crosswalks.verifySuccess", "Crosswalk verified"),
      });
      refetchCrosswalks();
    } catch (error: any) {
      toast({
        title: t("messages.error"),
        description: error?.response?.data?.detail || t("edim:crosswalks.verifyError", "Failed to verify"),
        variant: "destructive",
      });
    }
  };

  const handleRejectCrosswalk = async (crosswalkId: number) => {
    try {
      await rejectCrosswalkMutation.mutateAsync({ id: crosswalkId });
      toast({
        title: t("messages.success"),
        description: t("edim:crosswalks.rejectSuccess", "Crosswalk rejected"),
      });
      refetchCrosswalks();
    } catch (error: any) {
      toast({
        title: t("messages.error"),
        description: error?.response?.data?.detail || t("edim:crosswalks.rejectError", "Failed to reject"),
        variant: "destructive",
      });
    }
  };

  const getStatusVariant = (status: string) => {
    switch (status) {
      case "ACTIVE":
      case "VERIFIED":
        return "default";
      case "DRAFT":
      case "PROPOSED":
        return "secondary";
      case "DEPRECATED":
      case "REJECTED":
        return "outline";
      default:
        return "secondary";
    }
  };

  if (loadingSystem) {
    return (
      <AppLayout>
        <div className="flex h-64 items-center justify-center">
          <LoadingSpinner />
        </div>
      </AppLayout>
    );
  }

  if (!sourceSystem) {
    return (
      <AppLayout>
        <div className="text-center py-12">
          <p className="text-muted-foreground">{t("edim:sourceSystems.notFound", "Source system not found")}</p>
          <Link href="/settings/integrations">
            <Button variant="link">{t("actions.back", "Go back")}</Button>
          </Link>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Link href="/settings/integrations">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <PageHeader
            title={sourceSystem.name}
            subtitle={`${sourceSystem.code} - ${sourceSystem.system_type}`}
          />
        </div>

        <Tabs defaultValue="profiles" className="w-full">
          <TabsList>
            <TabsTrigger value="profiles">
              {t("edim:mappingProfiles.title", "Mapping Profiles")}
            </TabsTrigger>
            <TabsTrigger value="crosswalks">
              {t("edim:crosswalks.title", "Identity Crosswalks")}
            </TabsTrigger>
          </TabsList>

          {/* Mapping Profiles Tab */}
          <TabsContent value="profiles">
            <div className="space-y-4">
              {canManageMappings && (
                <div className="flex justify-end">
                  <Button onClick={openCreateProfileDialog}>
                    <Plus className="me-2 h-4 w-4" />
                    {t("edim:mappingProfiles.create", "Create Profile")}
                  </Button>
                </div>
              )}

              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("edim:mappingProfiles.name", "Name")}</TableHead>
                      <TableHead>{t("edim:mappingProfiles.documentType", "Document Type")}</TableHead>
                      <TableHead>{t("edim:mappingProfiles.version", "Version")}</TableHead>
                      <TableHead>{t("edim:mappingProfiles.postingPolicy", "Posting Policy")}</TableHead>
                      <TableHead>{t("edim:mappingProfiles.status", "Status")}</TableHead>
                      {canManageMappings && (
                        <TableHead className="w-32">{t("actions.actions", "Actions")}</TableHead>
                      )}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loadingProfiles ? (
                      <TableRow>
                        <TableCell colSpan={6} className="text-center py-8">
                          <LoadingSpinner />
                        </TableCell>
                      </TableRow>
                    ) : !mappingProfiles || mappingProfiles.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                          {t("edim:mappingProfiles.empty", "No mapping profiles defined yet")}
                        </TableCell>
                      </TableRow>
                    ) : (
                      mappingProfiles.map((profile) => (
                        <TableRow key={profile.id}>
                          <TableCell className="font-medium">{profile.name}</TableCell>
                          <TableCell>
                            {DOCUMENT_TYPE_OPTIONS.find((o) => o.value === profile.document_type)?.label || profile.document_type}
                          </TableCell>
                          <TableCell>v{profile.version}</TableCell>
                          <TableCell>
                            <Badge variant="outline">
                              {POSTING_POLICY_OPTIONS.find((o) => o.value === profile.posting_policy)?.label || profile.posting_policy}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Badge variant={getStatusVariant(profile.status)}>
                              {profile.status}
                            </Badge>
                          </TableCell>
                          {canManageMappings && (
                            <TableCell>
                              <div className="flex items-center gap-1">
                                {profile.status === "DRAFT" && (
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => handleActivateProfile(profile.id)}
                                    title={t("edim:mappingProfiles.activate", "Activate")}
                                  >
                                    <Play className="h-4 w-4 text-green-600" />
                                  </Button>
                                )}
                                {profile.status === "ACTIVE" && (
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => handleDeprecateProfile(profile.id)}
                                    title={t("edim:mappingProfiles.deprecate", "Deprecate")}
                                  >
                                    <Archive className="h-4 w-4 text-orange-600" />
                                  </Button>
                                )}
                              </div>
                            </TableCell>
                          )}
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>
          </TabsContent>

          {/* Crosswalks Tab */}
          <TabsContent value="crosswalks">
            <div className="space-y-4">
              {canManageCrosswalks && (
                <div className="flex justify-end">
                  <Button onClick={openCreateCrosswalkDialog}>
                    <Plus className="me-2 h-4 w-4" />
                    {t("edim:crosswalks.create", "Add Crosswalk")}
                  </Button>
                </div>
              )}

              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("edim:crosswalks.objectType", "Type")}</TableHead>
                      <TableHead>{t("edim:crosswalks.externalId", "External ID")}</TableHead>
                      <TableHead>{t("edim:crosswalks.externalLabel", "External Label")}</TableHead>
                      <TableHead>{t("edim:crosswalks.nxentraId", "Nxentra ID")}</TableHead>
                      <TableHead>{t("edim:crosswalks.status", "Status")}</TableHead>
                      {canManageCrosswalks && (
                        <TableHead className="w-32">{t("actions.actions", "Actions")}</TableHead>
                      )}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loadingCrosswalks ? (
                      <TableRow>
                        <TableCell colSpan={6} className="text-center py-8">
                          <LoadingSpinner />
                        </TableCell>
                      </TableRow>
                    ) : !crosswalks || crosswalks.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                          {t("edim:crosswalks.empty", "No identity crosswalks defined yet")}
                        </TableCell>
                      </TableRow>
                    ) : (
                      crosswalks.map((crosswalk) => (
                        <TableRow key={crosswalk.id}>
                          <TableCell>
                            <Badge variant="outline">
                              {OBJECT_TYPE_OPTIONS.find((o) => o.value === crosswalk.object_type)?.label || crosswalk.object_type}
                            </Badge>
                          </TableCell>
                          <TableCell className="font-mono text-sm">{crosswalk.external_id}</TableCell>
                          <TableCell>{crosswalk.external_label || "-"}</TableCell>
                          <TableCell className="font-mono text-sm">
                            {crosswalk.nxentra_id || <span className="text-muted-foreground">Not mapped</span>}
                          </TableCell>
                          <TableCell>
                            <Badge variant={getStatusVariant(crosswalk.status)}>
                              {crosswalk.status}
                            </Badge>
                          </TableCell>
                          {canManageCrosswalks && (
                            <TableCell>
                              <div className="flex items-center gap-1">
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => openEditCrosswalkDialog(crosswalk)}
                                  title={t("actions.edit", "Edit")}
                                >
                                  <Pencil className="h-4 w-4" />
                                </Button>
                                {crosswalk.status === "PROPOSED" && crosswalk.nxentra_id && (
                                  <>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      onClick={() => handleVerifyCrosswalk(crosswalk.id)}
                                      title={t("edim:crosswalks.verify", "Verify")}
                                    >
                                      <Check className="h-4 w-4 text-green-600" />
                                    </Button>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      onClick={() => handleRejectCrosswalk(crosswalk.id)}
                                      title={t("edim:crosswalks.reject", "Reject")}
                                    >
                                      <X className="h-4 w-4 text-red-600" />
                                    </Button>
                                  </>
                                )}
                              </div>
                            </TableCell>
                          )}
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>
          </TabsContent>
        </Tabs>

        {/* Create Profile Dialog */}
        <Dialog open={profileDialogOpen} onOpenChange={setProfileDialogOpen}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>{t("edim:mappingProfiles.create", "Create Mapping Profile")}</DialogTitle>
              <DialogDescription>
                {t("edim:mappingProfiles.createDescription", "Define how to transform external data into journal entries.")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>{t("edim:mappingProfiles.name", "Name")} *</Label>
                <Input
                  value={profileForm.name}
                  onChange={(e) => setProfileForm({ ...profileForm, name: e.target.value })}
                  placeholder="Daily Sales Import"
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("edim:mappingProfiles.documentType", "Document Type")}</Label>
                  <Select
                    value={profileForm.document_type}
                    onValueChange={(value) => setProfileForm({ ...profileForm, document_type: value as DocumentType })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {DOCUMENT_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>{t("edim:mappingProfiles.postingPolicy", "Posting Policy")}</Label>
                  <Select
                    value={profileForm.posting_policy}
                    onValueChange={(value) => setProfileForm({ ...profileForm, posting_policy: value as PostingPolicy })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {POSTING_POLICY_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setProfileDialogOpen(false)} disabled={createProfileMutation.isPending}>
                {t("actions.cancel")}
              </Button>
              <Button onClick={handleSaveProfile} disabled={createProfileMutation.isPending || !profileForm.name}>
                {createProfileMutation.isPending ? t("actions.loading") : t("actions.save")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Create/Edit Crosswalk Dialog */}
        <Dialog open={crosswalkDialogOpen} onOpenChange={setCrosswalkDialogOpen}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>
                {editingCrosswalk
                  ? t("edim:crosswalks.edit", "Edit Crosswalk")
                  : t("edim:crosswalks.create", "Add Crosswalk")}
              </DialogTitle>
              <DialogDescription>
                {t("edim:crosswalks.description", "Map external identifiers to Nxentra entities.")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("edim:crosswalks.objectType", "Object Type")}</Label>
                  <Select
                    value={crosswalkForm.object_type}
                    onValueChange={(value) => setCrosswalkForm({ ...crosswalkForm, object_type: value as CrosswalkObjectType })}
                    disabled={!!editingCrosswalk}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {OBJECT_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>{t("edim:crosswalks.externalId", "External ID")} *</Label>
                  <Input
                    value={crosswalkForm.external_id}
                    onChange={(e) => setCrosswalkForm({ ...crosswalkForm, external_id: e.target.value })}
                    placeholder="EXT-001"
                    disabled={!!editingCrosswalk}
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label>{t("edim:crosswalks.externalLabel", "External Label")}</Label>
                <Input
                  value={crosswalkForm.external_label || ""}
                  onChange={(e) => setCrosswalkForm({ ...crosswalkForm, external_label: e.target.value })}
                  placeholder="External system name for this ID"
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("edim:crosswalks.nxentraId", "Nxentra ID")}</Label>
                  {crosswalkForm.object_type === "ACCOUNT" && accounts ? (
                    <Select
                      value={crosswalkForm.nxentra_id || ""}
                      onValueChange={(value) => {
                        const account = accounts.find((a) => a.public_id === value);
                        setCrosswalkForm({
                          ...crosswalkForm,
                          nxentra_id: value,
                          nxentra_label: account ? `${account.code} - ${account.name}` : "",
                        });
                      }}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select account..." />
                      </SelectTrigger>
                      <SelectContent>
                        {accounts.map((account) => (
                          <SelectItem key={account.public_id} value={account.public_id}>
                            {account.code} - {account.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input
                      value={crosswalkForm.nxentra_id || ""}
                      onChange={(e) => setCrosswalkForm({ ...crosswalkForm, nxentra_id: e.target.value })}
                      placeholder="Nxentra public ID"
                    />
                  )}
                </div>
                <div className="space-y-2">
                  <Label>{t("edim:crosswalks.nxentraLabel", "Nxentra Label")}</Label>
                  <Input
                    value={crosswalkForm.nxentra_label || ""}
                    onChange={(e) => setCrosswalkForm({ ...crosswalkForm, nxentra_label: e.target.value })}
                    placeholder="Nxentra entity name"
                    disabled={crosswalkForm.object_type === "ACCOUNT"}
                  />
                </div>
              </div>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setCrosswalkDialogOpen(false)}
                disabled={createCrosswalkMutation.isPending || updateCrosswalkMutation.isPending}
              >
                {t("actions.cancel")}
              </Button>
              <Button
                onClick={handleSaveCrosswalk}
                disabled={createCrosswalkMutation.isPending || updateCrosswalkMutation.isPending || !crosswalkForm.external_id}
              >
                {createCrosswalkMutation.isPending || updateCrosswalkMutation.isPending ? t("actions.loading") : t("actions.save")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale || "en", ["common", "settings", "edim"])),
  },
});
