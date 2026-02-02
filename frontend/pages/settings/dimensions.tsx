import { useEffect, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { Plus, Pencil, Trash2 } from "lucide-react";
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
import { useBilingualText } from "@/components/common/BilingualText";
import { dimensionsService } from "@/services/accounts.service";
import type {
  AnalysisDimension,
  AnalysisDimensionValue,
  AnalysisDimensionCreatePayload,
  DimensionValueCreatePayload,
} from "@/types/account";

export default function DimensionsPage() {
  const { t } = useTranslation(["common", "accounting", "settings"]);
  const { toast } = useToast();
  const { hasPermission } = useAuth();
  const getText = useBilingualText();

  const [dimensions, setDimensions] = useState<AnalysisDimension[]>([]);
  const [loading, setLoading] = useState(true);

  // Tab 2: selected dimension for codes
  const [selectedDimensionId, setSelectedDimensionId] = useState<string>("");

  // Dimension dialog
  const [dimDialogOpen, setDimDialogOpen] = useState(false);
  const [editingDimension, setEditingDimension] = useState<AnalysisDimension | null>(null);
  const [dimForm, setDimForm] = useState<AnalysisDimensionCreatePayload>({
    code: "",
    name: "",
    name_ar: "",
    description: "",
    description_ar: "",
    is_required_on_posting: false,
    applies_to_account_types: [],
    display_order: 0,
  });
  const [dimSaving, setDimSaving] = useState(false);

  // Code dialog
  const [codeDialogOpen, setCodeDialogOpen] = useState(false);
  const [editingCode, setEditingCode] = useState<AnalysisDimensionValue | null>(null);
  const [codeForm, setCodeForm] = useState<DimensionValueCreatePayload>({
    code: "",
    name: "",
    name_ar: "",
    description: "",
    description_ar: "",
    parent_id: null,
  });
  const [codeSaving, setCodeSaving] = useState(false);

  // Delete confirm
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    type: "dimension" | "code";
    dimensionId: number;
    codeId?: number;
    label: string;
  } | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  const canManage = hasPermission("accounts.edit");

  const fetchDimensions = async () => {
    try {
      const { data } = await dimensionsService.list();
      setDimensions(data);
    } catch {
      toast({
        title: t("messages.error"),
        description: t("accounting:dimensions.loadError", "Failed to load dimensions"),
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDimensions();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Selected dimension for the Codes tab
  const selectedDimension = dimensions.find(
    (d) => d.id.toString() === selectedDimensionId
  );

  // --- Dimension Definition handlers ---
  const openCreateDimension = () => {
    setEditingDimension(null);
    setDimForm({
      code: "",
      name: "",
      name_ar: "",
      description: "",
      description_ar: "",
      is_required_on_posting: false,
      applies_to_account_types: [],
      display_order: dimensions.length,
    });
    setDimDialogOpen(true);
  };

  const openEditDimension = (dim: AnalysisDimension) => {
    setEditingDimension(dim);
    setDimForm({
      code: dim.code,
      name: dim.name,
      name_ar: dim.name_ar,
      description: dim.description,
      description_ar: dim.description_ar,
      is_required_on_posting: dim.is_required_on_posting,
      applies_to_account_types: dim.applies_to_account_types,
      display_order: dim.display_order,
    });
    setDimDialogOpen(true);
  };

  const handleSaveDimension = async () => {
    if (!dimForm.code || !dimForm.name) return;
    setDimSaving(true);
    try {
      if (editingDimension) {
        await dimensionsService.update(editingDimension.id, dimForm);
        toast({
          title: t("messages.success"),
          description: t("accounting:dimensions.updateSuccess", "Dimension updated"),
        });
      } else {
        await dimensionsService.create(dimForm);
        toast({
          title: t("messages.success"),
          description: t("accounting:dimensions.createSuccess", "Dimension created"),
        });
      }
      setDimDialogOpen(false);
      fetchDimensions();
    } catch {
      toast({
        title: t("messages.error"),
        description: t("accounting:dimensions.saveError", "Failed to save dimension"),
        variant: "destructive",
      });
    } finally {
      setDimSaving(false);
    }
  };

  // --- Dimension Code handlers ---
  const openCreateCode = () => {
    if (!selectedDimension) return;
    setEditingCode(null);
    setCodeForm({
      code: "",
      name: "",
      name_ar: "",
      description: "",
      description_ar: "",
      parent_id: null,
    });
    setCodeDialogOpen(true);
  };

  const openEditCode = (value: AnalysisDimensionValue) => {
    setEditingCode(value);
    setCodeForm({
      code: value.code,
      name: value.name,
      name_ar: value.name_ar,
      description: value.description,
      description_ar: value.description_ar,
      parent_id: value.parent,
    });
    setCodeDialogOpen(true);
  };

  const handleSaveCode = async () => {
    if (!selectedDimension || !codeForm.code || !codeForm.name) return;
    setCodeSaving(true);
    try {
      if (editingCode) {
        await dimensionsService.updateValue(selectedDimension.id, editingCode.id, codeForm);
        toast({
          title: t("messages.success"),
          description: t("accounting:dimensions.codeUpdateSuccess", "Code updated"),
        });
      } else {
        await dimensionsService.createValue(selectedDimension.id, codeForm);
        toast({
          title: t("messages.success"),
          description: t("accounting:dimensions.codeCreateSuccess", "Code created"),
        });
      }
      setCodeDialogOpen(false);
      fetchDimensions();
    } catch {
      toast({
        title: t("messages.error"),
        description: t("accounting:dimensions.codeSaveError", "Failed to save code"),
        variant: "destructive",
      });
    } finally {
      setCodeSaving(false);
    }
  };

  // --- Delete handler ---
  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    try {
      if (deleteTarget.type === "dimension") {
        await dimensionsService.delete(deleteTarget.dimensionId);
        if (selectedDimensionId === deleteTarget.dimensionId.toString()) {
          setSelectedDimensionId("");
        }
        toast({
          title: t("messages.success"),
          description: t("accounting:dimensions.deleteSuccess", "Dimension deleted"),
        });
      } else if (deleteTarget.codeId) {
        await dimensionsService.deleteValue(deleteTarget.dimensionId, deleteTarget.codeId);
        toast({
          title: t("messages.success"),
          description: t("accounting:dimensions.codeDeleteSuccess", "Code deleted"),
        });
      }
      setDeleteConfirmOpen(false);
      fetchDimensions();
    } catch {
      toast({
        title: t("messages.error"),
        description: t("accounting:dimensions.deleteError", "Failed to delete"),
        variant: "destructive",
      });
    } finally {
      setDeleteLoading(false);
    }
  };

  const getDefinitionTypeLabel = (dim: AnalysisDimension) => {
    if (dim.applies_to_account_types.length > 0) {
      return t("accounting:dimensions.typeChart", "Chart of Accounts");
    }
    return t("accounting:dimensions.typeJournal", "Journal Entry");
  };

  if (loading) {
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
      <div className="space-y-6 p-6">
        <PageHeader
          title={t("accounting:dimensions.title", "Analysis Dimensions")}
          subtitle={t("accounting:dimensions.subtitle", "Define analysis codes for cost tracking and reporting")}
        />

        <Tabs defaultValue="definition" className="w-full">
          <TabsList>
            <TabsTrigger value="definition">
              {t("accounting:dimensions.definitionTab", "Dimension Definition")}
            </TabsTrigger>
            <TabsTrigger value="codes">
              {t("accounting:dimensions.codesTab", "Dimension Codes")}
            </TabsTrigger>
          </TabsList>

          {/* Tab 1: Dimension Definition */}
          <TabsContent value="definition">
            <div className="space-y-4">
              {canManage && (
                <div className="flex justify-end">
                  <Button onClick={openCreateDimension}>
                    <Plus className="me-2 h-4 w-4" />
                    {t("accounting:dimensions.create", "Create Dimension")}
                  </Button>
                </div>
              )}

              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("accounting:dimensions.code", "Code")}</TableHead>
                      <TableHead>{t("accounting:dimensions.name", "Name")}</TableHead>
                      <TableHead>{t("accounting:dimensions.definitionType", "Definition Type")}</TableHead>
                      <TableHead>{t("accounting:dimensions.required", "Required")}</TableHead>
                      <TableHead>{t("accounting:dimensions.codesCount", "Codes")}</TableHead>
                      <TableHead>{t("accounting:dimensions.status", "Status")}</TableHead>
                      {canManage && (
                        <TableHead className="w-24">{t("actions.actions", "Actions")}</TableHead>
                      )}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {dimensions.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={canManage ? 7 : 6} className="text-center text-muted-foreground py-8">
                          {t("accounting:dimensions.empty", "No analysis dimensions defined yet")}
                        </TableCell>
                      </TableRow>
                    ) : (
                      dimensions.map((dim) => (
                        <TableRow key={dim.id}>
                          <TableCell className="font-mono text-sm">{dim.code}</TableCell>
                          <TableCell>{getText(dim.name, dim.name_ar)}</TableCell>
                          <TableCell>
                            <Badge variant={dim.applies_to_account_types.length > 0 ? "default" : "secondary"}>
                              {getDefinitionTypeLabel(dim)}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            {dim.is_required_on_posting ? (
                              <Badge variant="destructive">{t("accounting:dimensions.requiredYes", "Required")}</Badge>
                            ) : (
                              <span className="text-muted-foreground text-sm">{t("accounting:dimensions.optional", "Optional")}</span>
                            )}
                          </TableCell>
                          <TableCell>{dim.values?.length || 0}</TableCell>
                          <TableCell>
                            <Badge variant={dim.is_active ? "default" : "secondary"}>
                              {dim.is_active ? t("status.active", "Active") : t("status.inactive", "Inactive")}
                            </Badge>
                          </TableCell>
                          {canManage && (
                            <TableCell>
                              <div className="flex items-center gap-1">
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => openEditDimension(dim)}
                                >
                                  <Pencil className="h-4 w-4" />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => {
                                    setDeleteTarget({
                                      type: "dimension",
                                      dimensionId: dim.id,
                                      label: dim.code,
                                    });
                                    setDeleteConfirmOpen(true);
                                  }}
                                >
                                  <Trash2 className="h-4 w-4 text-destructive" />
                                </Button>
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

          {/* Tab 2: Dimension Codes */}
          <TabsContent value="codes">
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-4">
                <div className="w-72">
                  <Select
                    value={selectedDimensionId}
                    onValueChange={setSelectedDimensionId}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t("accounting:dimensions.selectDimension", "Select a dimension")} />
                    </SelectTrigger>
                    <SelectContent>
                      {dimensions.map((dim) => (
                        <SelectItem key={dim.id} value={dim.id.toString()}>
                          {dim.code} - {getText(dim.name, dim.name_ar)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {canManage && selectedDimension && (
                  <Button onClick={openCreateCode}>
                    <Plus className="me-2 h-4 w-4" />
                    {t("accounting:dimensions.addCode", "Add Code")}
                  </Button>
                )}
              </div>

              {!selectedDimension ? (
                <div className="rounded-lg border py-12 text-center text-muted-foreground">
                  {t("accounting:dimensions.selectDimensionHint", "Select a dimension above to manage its codes")}
                </div>
              ) : (
                <>
                  {/* Dimension info summary */}
                  <div className="flex items-center gap-3 text-sm text-muted-foreground">
                    <Badge variant={selectedDimension.applies_to_account_types.length > 0 ? "default" : "secondary"}>
                      {getDefinitionTypeLabel(selectedDimension)}
                    </Badge>
                  </div>

                  {/* Codes table */}
                  <div className="rounded-lg border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>{t("accounting:dimensions.codeValue", "Code")}</TableHead>
                          <TableHead>{t("accounting:dimensions.codeName", "Name")}</TableHead>
                          <TableHead>{t("accounting:dimensions.codeDescription", "Description")}</TableHead>
                          <TableHead>{t("accounting:dimensions.status", "Status")}</TableHead>
                          {canManage && (
                            <TableHead className="w-24">{t("actions.actions", "Actions")}</TableHead>
                          )}
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(!selectedDimension.values || selectedDimension.values.length === 0) ? (
                          <TableRow>
                            <TableCell colSpan={canManage ? 5 : 4} className="text-center text-muted-foreground py-8">
                              {t("accounting:dimensions.noCodes", "No codes defined for this dimension")}
                            </TableCell>
                          </TableRow>
                        ) : (
                          selectedDimension.values.map((val) => (
                            <TableRow key={val.id}>
                              <TableCell className="font-mono text-sm">{val.code}</TableCell>
                              <TableCell>{getText(val.name, val.name_ar)}</TableCell>
                              <TableCell className="text-sm text-muted-foreground">
                                {getText(val.description, val.description_ar) || "-"}
                              </TableCell>
                              <TableCell>
                                <Badge variant={val.is_active ? "default" : "secondary"}>
                                  {val.is_active ? t("status.active", "Active") : t("status.inactive", "Inactive")}
                                </Badge>
                              </TableCell>
                              {canManage && (
                                <TableCell>
                                  <div className="flex items-center gap-1">
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      onClick={() => openEditCode(val)}
                                    >
                                      <Pencil className="h-4 w-4" />
                                    </Button>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      onClick={() => {
                                        setDeleteTarget({
                                          type: "code",
                                          dimensionId: selectedDimension.id,
                                          codeId: val.id,
                                          label: val.code,
                                        });
                                        setDeleteConfirmOpen(true);
                                      }}
                                    >
                                      <Trash2 className="h-4 w-4 text-destructive" />
                                    </Button>
                                  </div>
                                </TableCell>
                              )}
                            </TableRow>
                          ))
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </>
              )}
            </div>
          </TabsContent>
        </Tabs>

        {/* Dimension Create/Edit Dialog */}
        <Dialog open={dimDialogOpen} onOpenChange={setDimDialogOpen}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>
                {editingDimension
                  ? t("accounting:dimensions.edit", "Edit Dimension")
                  : t("accounting:dimensions.create", "Create Dimension")}
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.code", "Code")} *</Label>
                  <Input
                    value={dimForm.code}
                    onChange={(e) => setDimForm({ ...dimForm, code: e.target.value.toUpperCase() })}
                    placeholder="COST_CENTER"
                    disabled={!!editingDimension}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.displayOrder", "Display Order")}</Label>
                  <Input
                    type="number"
                    value={dimForm.display_order || 0}
                    onChange={(e) => setDimForm({ ...dimForm, display_order: parseInt(e.target.value) || 0 })}
                  />
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.name", "Name")} *</Label>
                  <Input
                    value={dimForm.name}
                    onChange={(e) => setDimForm({ ...dimForm, name: e.target.value })}
                    placeholder="Cost Center"
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.nameAr", "Name (Arabic)")}</Label>
                  <Input
                    value={dimForm.name_ar || ""}
                    onChange={(e) => setDimForm({ ...dimForm, name_ar: e.target.value })}
                    placeholder="مركز التكلفة"
                    dir="rtl"
                  />
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.description", "Description")}</Label>
                  <Input
                    value={dimForm.description || ""}
                    onChange={(e) => setDimForm({ ...dimForm, description: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.descriptionAr", "Description (Arabic)")}</Label>
                  <Input
                    value={dimForm.description_ar || ""}
                    onChange={(e) => setDimForm({ ...dimForm, description_ar: e.target.value })}
                    dir="rtl"
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label>{t("accounting:dimensions.definitionType", "Definition Type")}</Label>
                <Select
                  value={dimForm.applies_to_account_types && dimForm.applies_to_account_types.length > 0 ? "chart" : "journal"}
                  onValueChange={(value) => {
                    if (value === "journal") {
                      setDimForm({ ...dimForm, applies_to_account_types: [] });
                    } else {
                      // Default to common revenue/expense types for Chart of Accounts dimensions
                      setDimForm({ ...dimForm, applies_to_account_types: ["REVENUE", "EXPENSE"] });
                    }
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="chart">{t("accounting:dimensions.typeChart", "Chart of Accounts")}</SelectItem>
                    <SelectItem value="journal">{t("accounting:dimensions.typeJournal", "Journal Entry")}</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {dimForm.applies_to_account_types && dimForm.applies_to_account_types.length > 0
                    ? t("accounting:dimensions.typeChartDesc", "Appears as a dropdown in chart of accounts to select one code per account.")
                    : t("accounting:dimensions.typeJournalDesc", "Appears as a checkbox in chart of accounts. When checked, this dimension is available during journal entry.")}
                </p>
              </div>

              {/* Account Types selection - only shown for Chart of Accounts type */}
              {dimForm.applies_to_account_types && dimForm.applies_to_account_types.length > 0 && (
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.appliesToAccountTypes", "Applies to Account Types")}</Label>
                  <div className="grid grid-cols-2 gap-2 p-3 border rounded-md bg-muted/30">
                    {(["ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE", "RECEIVABLE", "PAYABLE", "MEMO"] as const).map((type) => (
                      <label key={type} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={dimForm.applies_to_account_types?.includes(type) || false}
                          onChange={(e) => {
                            const current = dimForm.applies_to_account_types || [];
                            if (e.target.checked) {
                              setDimForm({ ...dimForm, applies_to_account_types: [...current, type] });
                            } else {
                              const updated = current.filter((t) => t !== type);
                              // Keep at least one type selected for Chart of Accounts type
                              if (updated.length > 0) {
                                setDimForm({ ...dimForm, applies_to_account_types: updated });
                              }
                            }
                          }}
                          className="h-4 w-4"
                        />
                        {t(`accounting:accountTypes.${type}`, type)}
                      </label>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {t("accounting:dimensions.appliesToDesc", "Select which account types this dimension can be assigned to.")}
                  </p>
                </div>
              )}

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="is_required"
                  checked={dimForm.is_required_on_posting || false}
                  onChange={(e) => setDimForm({ ...dimForm, is_required_on_posting: e.target.checked })}
                  className="h-4 w-4"
                />
                <Label htmlFor="is_required">
                  {t("accounting:dimensions.requiredOnPosting", "Required when posting journal entries")}
                </Label>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDimDialogOpen(false)} disabled={dimSaving}>
                {t("actions.cancel")}
              </Button>
              <Button onClick={handleSaveDimension} disabled={dimSaving || !dimForm.code || !dimForm.name}>
                {dimSaving ? t("actions.loading") : t("actions.save")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Code Create/Edit Dialog */}
        <Dialog open={codeDialogOpen} onOpenChange={setCodeDialogOpen}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>
                {editingCode
                  ? t("accounting:dimensions.editCode", "Edit Code")
                  : t("accounting:dimensions.addCode", "Add Code")}
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>{t("accounting:dimensions.codeValue", "Code")} *</Label>
                <Input
                  value={codeForm.code}
                  onChange={(e) => setCodeForm({ ...codeForm, code: e.target.value.toUpperCase() })}
                  placeholder="C0001"
                  disabled={!!editingCode}
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.codeName", "Name")} *</Label>
                  <Input
                    value={codeForm.name}
                    onChange={(e) => setCodeForm({ ...codeForm, name: e.target.value })}
                    placeholder="Sales Department"
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.codeNameAr", "Name (Arabic)")}</Label>
                  <Input
                    value={codeForm.name_ar || ""}
                    onChange={(e) => setCodeForm({ ...codeForm, name_ar: e.target.value })}
                    placeholder="قسم المبيعات"
                    dir="rtl"
                  />
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.codeDescription", "Description")}</Label>
                  <Input
                    value={codeForm.description || ""}
                    onChange={(e) => setCodeForm({ ...codeForm, description: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label>{t("accounting:dimensions.codeDescriptionAr", "Description (Arabic)")}</Label>
                  <Input
                    value={codeForm.description_ar || ""}
                    onChange={(e) => setCodeForm({ ...codeForm, description_ar: e.target.value })}
                    dir="rtl"
                  />
                </div>
              </div>
              {selectedDimension && (() => {
                const parentCodes = selectedDimension.values?.filter(v => v.id !== editingCode?.id) || [];
                if (parentCodes.length === 0) return null;
                return (
                  <div className="space-y-2">
                    <Label>{t("accounting:dimensions.parentCode", "Parent Code")}</Label>
                    <Select
                      value={codeForm.parent_id?.toString() || "none"}
                      onValueChange={(value) => setCodeForm({ ...codeForm, parent_id: value === "none" ? null : parseInt(value) })}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder={t("accounting:dimensions.noParent", "None (top-level)")} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">{t("accounting:dimensions.noParent", "None (top-level)")}</SelectItem>
                        {parentCodes.map((pc) => (
                          <SelectItem key={pc.id} value={pc.id.toString()}>
                            {pc.code} - {getText(pc.name, pc.name_ar)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                );
              })()}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setCodeDialogOpen(false)} disabled={codeSaving}>
                {t("actions.cancel")}
              </Button>
              <Button onClick={handleSaveCode} disabled={codeSaving || !codeForm.code || !codeForm.name}>
                {codeSaving ? t("actions.loading") : t("actions.save")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Delete Confirm Dialog */}
        <ConfirmDialog
          open={deleteConfirmOpen}
          onOpenChange={setDeleteConfirmOpen}
          title={t("accounting:dimensions.deleteConfirmTitle", "Delete")}
          description={t("accounting:dimensions.deleteConfirmDescription", "Are you sure you want to delete \"{{label}}\"? This action cannot be undone.", { label: deleteTarget?.label || "" })}
          variant="destructive"
          onConfirm={handleDelete}
          isLoading={deleteLoading}
        />
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale || "en", ["common", "accounting", "settings"])),
  },
});
