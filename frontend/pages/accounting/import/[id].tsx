import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { Collapsible } from "@/components/ui/collapsible";
import { useToast } from "@/components/ui/toaster";
import {
  useBatch,
  useBatchRecords,
  useMappingProfiles,
  useMapBatch,
  useValidateBatch,
  usePreviewBatch,
  useCommitBatch,
  useRejectBatch,
} from "@/queries/useEdim";
import {
  ArrowLeft,
  FileText,
  CheckCircle,
  AlertCircle,
  XCircle,
  Play,
  Check,
  Loader2,
  ArrowRight,
  BookOpen,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";
import type {
  BatchStatus,
  MappingProfile,
  StagedRecord,
  BatchPreviewResponse,
  BatchPreviewEntry,
} from "@/types/edim";

const WORKFLOW_STEPS = [
  { key: "STAGED", label: "Staged", description: "File parsed and records created" },
  { key: "MAPPED", label: "Mapped", description: "Mapping profile applied" },
  { key: "VALIDATED", label: "Validated", description: "Records validated" },
  { key: "PREVIEWED", label: "Previewed", description: "Journal entries previewed" },
  { key: "COMMITTED", label: "Committed", description: "Journal entries created" },
];

const STATUS_ORDER: Record<BatchStatus, number> = {
  STAGED: 0,
  MAPPED: 1,
  VALIDATED: 2,
  PREVIEWED: 3,
  COMMITTED: 4,
  REJECTED: -1,
};

function WorkflowStepper({ currentStatus }: { currentStatus: BatchStatus }) {
  const currentIndex = STATUS_ORDER[currentStatus];
  const isRejected = currentStatus === "REJECTED";

  return (
    <div className="flex items-center justify-between w-full">
      {WORKFLOW_STEPS.map((step, index) => {
        const isCompleted = !isRejected && currentIndex >= index;
        const isCurrent = !isRejected && currentIndex === index;
        const isPending = !isRejected && currentIndex < index;

        return (
          <div key={step.key} className="flex items-center flex-1">
            <div className="flex flex-col items-center">
              <div
                className={cn(
                  "flex items-center justify-center w-10 h-10 rounded-full border-2 transition-colors",
                  isCompleted && !isCurrent && "bg-primary border-primary text-primary-foreground",
                  isCurrent && "border-primary bg-primary/10 text-primary",
                  isPending && "border-muted-foreground/30 text-muted-foreground",
                  isRejected && "border-muted-foreground/30 text-muted-foreground"
                )}
              >
                {isCompleted && !isCurrent ? (
                  <Check className="h-5 w-5" />
                ) : (
                  <span className="text-sm font-medium">{index + 1}</span>
                )}
              </div>
              <div className="mt-2 text-center">
                <p
                  className={cn(
                    "text-sm font-medium",
                    (isCurrent || isCompleted) && !isRejected
                      ? "text-foreground"
                      : "text-muted-foreground"
                  )}
                >
                  {step.label}
                </p>
                <p className="text-xs text-muted-foreground hidden sm:block">
                  {step.description}
                </p>
              </div>
            </div>
            {index < WORKFLOW_STEPS.length - 1 && (
              <div
                className={cn(
                  "flex-1 h-0.5 mx-2",
                  isCompleted && !isCurrent && !isRejected
                    ? "bg-primary"
                    : "bg-muted-foreground/30"
                )}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function BatchDetailPage() {
  const { t } = useTranslation(["common", "edim"]);
  const router = useRouter();
  const { toast } = useToast();
  const { id } = router.query;
  const batchId = typeof id === "string" ? parseInt(id) : 0;

  const [mapDialogOpen, setMapDialogOpen] = useState(false);
  const [selectedProfileId, setSelectedProfileId] = useState<string>("");
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [commitDialogOpen, setCommitDialogOpen] = useState(false);
  const [previewData, setPreviewData] = useState<BatchPreviewResponse | null>(null);

  const { data: batch, isLoading: loadingBatch, refetch: refetchBatch } = useBatch(batchId);
  const { data: records, isLoading: loadingRecords } = useBatchRecords(batchId, 1, 100);
  const { data: mappingProfiles } = useMappingProfiles(
    batch?.source_system ? { source_system: batch.source_system } : undefined
  );

  const mapBatch = useMapBatch();
  const validateBatch = useValidateBatch();
  const previewBatch = usePreviewBatch();
  const commitBatch = useCommitBatch();
  const rejectBatch = useRejectBatch();

  const activeProfiles = mappingProfiles?.filter((p: MappingProfile) => p.status === "ACTIVE") || [];

  const handleMap = async () => {
    if (!selectedProfileId) {
      toast({
        title: t("common:error"),
        description: t("edim:batch.selectProfile", "Please select a mapping profile"),
        variant: "destructive",
      });
      return;
    }

    try {
      await mapBatch.mutateAsync({
        batchId,
        mappingProfileId: parseInt(selectedProfileId),
      });
      toast({
        title: t("common:success"),
        description: t("edim:batch.mapSuccess", "Batch mapped successfully"),
      });
      setMapDialogOpen(false);
      refetchBatch();
    } catch {
      toast({
        title: t("common:error"),
        description: t("edim:batch.mapError", "Failed to map batch"),
        variant: "destructive",
      });
    }
  };

  const handleValidate = async () => {
    try {
      await validateBatch.mutateAsync(batchId);
      toast({
        title: t("common:success"),
        description: t("edim:batch.validateSuccess", "Batch validated successfully"),
      });
      refetchBatch();
    } catch {
      toast({
        title: t("common:error"),
        description: t("edim:batch.validateError", "Failed to validate batch"),
        variant: "destructive",
      });
    }
  };

  const handlePreview = async () => {
    try {
      const result = await previewBatch.mutateAsync(batchId);
      setPreviewData(result.data);
      toast({
        title: t("common:success"),
        description: t("edim:batch.previewSuccess", "Preview generated successfully"),
      });
      refetchBatch();
    } catch {
      toast({
        title: t("common:error"),
        description: t("edim:batch.previewError", "Failed to generate preview"),
        variant: "destructive",
      });
    }
  };

  const handleCommit = async () => {
    try {
      await commitBatch.mutateAsync(batchId);
      toast({
        title: t("common:success"),
        description: t("edim:batch.commitSuccess", "Batch committed successfully. Journal entries created."),
      });
      setCommitDialogOpen(false);
      refetchBatch();
    } catch {
      toast({
        title: t("common:error"),
        description: t("edim:batch.commitError", "Failed to commit batch"),
        variant: "destructive",
      });
    }
  };

  const handleReject = async () => {
    try {
      await rejectBatch.mutateAsync({
        batchId,
        reason: rejectReason || undefined,
      });
      toast({
        title: t("common:success"),
        description: t("edim:batch.rejectSuccess", "Batch rejected"),
      });
      setRejectDialogOpen(false);
      setRejectReason("");
      refetchBatch();
    } catch {
      toast({
        title: t("common:error"),
        description: t("edim:batch.rejectError", "Failed to reject batch"),
        variant: "destructive",
      });
    }
  };

  const formatFileSize = (bytes: number | null) => {
    if (!bytes) return "-";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "-";
    return new Date(dateStr).toLocaleString();
  };

  const getStatusBadge = (status: BatchStatus) => {
    const variants: Record<BatchStatus, "default" | "secondary" | "destructive" | "outline"> = {
      STAGED: "secondary",
      MAPPED: "secondary",
      VALIDATED: "outline",
      PREVIEWED: "outline",
      COMMITTED: "default",
      REJECTED: "destructive",
    };
    return <Badge variant={variants[status]}>{status}</Badge>;
  };

  const canMap = batch?.status === "STAGED";
  const canValidate = batch?.status === "MAPPED";
  const canPreview = batch?.status === "VALIDATED";
  const canCommit = batch?.status === "PREVIEWED" || (batch?.status === "VALIDATED" && batch?.mapping_profile);
  const canReject = batch && !["COMMITTED", "REJECTED"].includes(batch.status);
  const isTerminal = batch?.status === "COMMITTED" || batch?.status === "REJECTED";

  if (loadingBatch) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      </AppLayout>
    );
  }

  if (!batch) {
    return (
      <AppLayout>
        <div className="flex flex-col items-center justify-center py-12">
          <AlertCircle className="h-12 w-12 text-muted-foreground mb-4" />
          <h3 className="text-lg font-medium">{t("edim:batch.notFound", "Batch not found")}</h3>
          <Button asChild className="mt-4">
            <Link href="/accounting/import">{t("common:back")}</Link>
          </Button>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={batch.original_filename}
          subtitle={t("edim:batch.subtitle", "Import batch from {{source}}", {
            source: batch.source_system_name,
          })}
          actions={
            <div className="flex items-center gap-2">
              {canReject && (
                <Button variant="outline" onClick={() => setRejectDialogOpen(true)}>
                  <XCircle className="mr-2 h-4 w-4" />
                  {t("common:reject", "Reject")}
                </Button>
              )}
              <Button variant="outline" asChild>
                <Link href="/accounting/import">
                  <ArrowLeft className="mr-2 h-4 w-4" />
                  {t("common:back")}
                </Link>
              </Button>
            </div>
          }
        />

        {/* Status Banner for Rejected */}
        {batch.status === "REJECTED" && (
          <Card className="border-destructive bg-destructive/5">
            <CardContent className="flex items-center gap-3 py-4">
              <XCircle className="h-5 w-5 text-destructive" />
              <div>
                <p className="font-medium text-destructive">
                  {t("edim:batch.rejected", "This batch has been rejected")}
                </p>
                {batch.rejection_reason && (
                  <p className="text-sm text-muted-foreground mt-1">
                    {t("edim:batch.reason", "Reason")}: {batch.rejection_reason}
                  </p>
                )}
                <p className="text-xs text-muted-foreground mt-1">
                  {t("edim:batch.rejectedBy", "Rejected by {{email}} on {{date}}", {
                    email: batch.rejected_by_email || "Unknown",
                    date: formatDate(batch.rejected_at),
                  })}
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Status Banner for Committed */}
        {batch.status === "COMMITTED" && (
          <Card className="border-green-500 bg-green-500/5">
            <CardContent className="flex items-center justify-between py-4">
              <div className="flex items-center gap-3">
                <CheckCircle className="h-5 w-5 text-green-600" />
                <div>
                  <p className="font-medium text-green-700">
                    {t("edim:batch.committed", "This batch has been committed")}
                  </p>
                  <p className="text-sm text-muted-foreground mt-1">
                    {t("edim:batch.committedBy", "Committed by {{email}} on {{date}}", {
                      email: batch.committed_by_email || "Unknown",
                      date: formatDate(batch.committed_at),
                    })}
                  </p>
                </div>
              </div>
              {batch.committed_entry_public_ids?.length > 0 && (
                <Button variant="outline" asChild>
                  <Link href="/accounting/journal-entries">
                    <BookOpen className="mr-2 h-4 w-4" />
                    {t("edim:batch.viewEntries", "View Journal Entries")}
                  </Link>
                </Button>
              )}
            </CardContent>
          </Card>
        )}

        {/* Workflow Stepper */}
        {!isTerminal && (
          <Card>
            <CardHeader>
              <CardTitle>{t("edim:batch.workflow", "Import Workflow")}</CardTitle>
            </CardHeader>
            <CardContent>
              <WorkflowStepper currentStatus={batch.status} />
            </CardContent>
          </Card>
        )}

        {/* Batch Info & Actions */}
        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>{t("edim:batch.info", "Batch Information")}</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                <div>
                  <dt className="text-sm text-muted-foreground">
                    {t("edim:batch.status", "Status")}
                  </dt>
                  <dd className="mt-1">{getStatusBadge(batch.status)}</dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">
                    {t("edim:batch.sourceSystem", "Source System")}
                  </dt>
                  <dd className="mt-1 font-medium">{batch.source_system_name}</dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">
                    {t("edim:batch.mappingProfile", "Mapping Profile")}
                  </dt>
                  <dd className="mt-1 font-medium">
                    {batch.mapping_profile_name || "-"}
                    {batch.mapping_profile_version && (
                      <span className="text-muted-foreground ml-1">
                        v{batch.mapping_profile_version}
                      </span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">
                    {t("edim:batch.fileSize", "File Size")}
                  </dt>
                  <dd className="mt-1">{formatFileSize(batch.file_size_bytes)}</dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">
                    {t("edim:batch.totalRecords", "Total Records")}
                  </dt>
                  <dd className="mt-1 font-medium">{batch.total_records}</dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">
                    {t("edim:batch.validRecords", "Valid Records")}
                  </dt>
                  <dd className="mt-1">
                    <span className="font-medium">{batch.validated_records}</span>
                    {batch.error_count > 0 && (
                      <span className="text-destructive ml-2">
                        ({batch.error_count} errors)
                      </span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">
                    {t("edim:batch.uploadedBy", "Uploaded By")}
                  </dt>
                  <dd className="mt-1">{batch.staged_by_email || "-"}</dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">
                    {t("edim:batch.uploadedAt", "Uploaded At")}
                  </dt>
                  <dd className="mt-1">{formatDate(batch.created_at)}</dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          {/* Actions Card */}
          {!isTerminal && (
            <Card>
              <CardHeader>
                <CardTitle>{t("edim:batch.actions", "Actions")}</CardTitle>
                <CardDescription>
                  {t("edim:batch.actionsDesc", "Process this batch through the import workflow")}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {canMap && (
                  <Button
                    className="w-full justify-start"
                    onClick={() => setMapDialogOpen(true)}
                    disabled={mapBatch.isPending}
                  >
                    <Play className="mr-2 h-4 w-4" />
                    {t("edim:batch.mapAction", "Apply Mapping Profile")}
                    <ArrowRight className="ml-auto h-4 w-4" />
                  </Button>
                )}
                {canValidate && (
                  <Button
                    className="w-full justify-start"
                    onClick={handleValidate}
                    disabled={validateBatch.isPending}
                  >
                    {validateBatch.isPending ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <CheckCircle className="mr-2 h-4 w-4" />
                    )}
                    {t("edim:batch.validateAction", "Validate Records")}
                    <ArrowRight className="ml-auto h-4 w-4" />
                  </Button>
                )}
                {canPreview && (
                  <Button
                    className="w-full justify-start"
                    onClick={handlePreview}
                    disabled={previewBatch.isPending}
                  >
                    {previewBatch.isPending ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <FileText className="mr-2 h-4 w-4" />
                    )}
                    {t("edim:batch.previewAction", "Preview Journal Entries")}
                    <ArrowRight className="ml-auto h-4 w-4" />
                  </Button>
                )}
                {canCommit && (
                  <Button
                    className="w-full justify-start"
                    variant="default"
                    onClick={() => setCommitDialogOpen(true)}
                    disabled={commitBatch.isPending}
                  >
                    <Check className="mr-2 h-4 w-4" />
                    {t("edim:batch.commitAction", "Commit to Accounting")}
                    <ArrowRight className="ml-auto h-4 w-4" />
                  </Button>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        {/* Records & Preview Tabs */}
        <Card>
          <Tabs defaultValue="records">
            <CardHeader>
              <TabsList>
                <TabsTrigger value="records">
                  {t("edim:batch.recordsTab", "Records")} ({batch.total_records})
                </TabsTrigger>
                {(previewData || batch.status === "PREVIEWED") && (
                  <TabsTrigger value="preview">
                    {t("edim:batch.previewTab", "Preview")}
                  </TabsTrigger>
                )}
              </TabsList>
            </CardHeader>
            <CardContent>
              <TabsContent value="records" className="mt-0">
                {loadingRecords ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                ) : !records?.records?.length ? (
                  <div className="text-center py-8 text-muted-foreground">
                    {t("edim:batch.noRecords", "No records found")}
                  </div>
                ) : (
                  <div className="rounded-md border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-16">{t("edim:batch.row", "Row")}</TableHead>
                          <TableHead>{t("edim:batch.rawData", "Raw Data")}</TableHead>
                          <TableHead>{t("edim:batch.mappedData", "Mapped Data")}</TableHead>
                          <TableHead className="w-24">{t("edim:batch.valid", "Valid")}</TableHead>
                          <TableHead>{t("edim:batch.errors", "Errors")}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {records.records.map((record: StagedRecord) => (
                          <TableRow key={record.id}>
                            <TableCell className="font-mono text-sm">
                              {record.row_number}
                            </TableCell>
                            <TableCell>
                              <Collapsible
                                trigger={t("edim:batch.viewRaw", "View raw data")}
                              >
                                <pre className="text-xs bg-muted p-2 rounded overflow-auto max-h-32">
                                  {JSON.stringify(record.raw_payload, null, 2)}
                                </pre>
                              </Collapsible>
                            </TableCell>
                            <TableCell>
                              {record.mapped_payload ? (
                                <Collapsible
                                  trigger={t("edim:batch.viewMapped", "View mapped data")}
                                >
                                  <pre className="text-xs bg-muted p-2 rounded overflow-auto max-h-32">
                                    {JSON.stringify(record.mapped_payload, null, 2)}
                                  </pre>
                                </Collapsible>
                              ) : (
                                <span className="text-muted-foreground text-sm">-</span>
                              )}
                            </TableCell>
                            <TableCell>
                              {record.is_valid === null ? (
                                <span className="text-muted-foreground">-</span>
                              ) : record.is_valid ? (
                                <CheckCircle className="h-4 w-4 text-green-600" />
                              ) : (
                                <XCircle className="h-4 w-4 text-destructive" />
                              )}
                            </TableCell>
                            <TableCell>
                              {(record.mapping_errors?.length > 0 ||
                                record.validation_errors?.length > 0) && (
                                <ul className="text-xs text-destructive space-y-1">
                                  {record.mapping_errors?.map((err, i) => (
                                    <li key={`map-${i}`}>{err}</li>
                                  ))}
                                  {record.validation_errors?.map((err, i) => (
                                    <li key={`val-${i}`}>{err}</li>
                                  ))}
                                </ul>
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="preview" className="mt-0">
                {previewData?.preview ? (
                  <div className="space-y-4">
                    <div className="flex items-center gap-6 text-sm">
                      <div>
                        <span className="text-muted-foreground">
                          {t("edim:batch.totalEntries", "Total Entries")}:
                        </span>{" "}
                        <span className="font-medium">{previewData.preview.total_entries}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">
                          {t("edim:batch.totalDebit", "Total Debit")}:
                        </span>{" "}
                        <span className="font-medium">{previewData.preview.total_debit}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">
                          {t("edim:batch.totalCredit", "Total Credit")}:
                        </span>{" "}
                        <span className="font-medium">{previewData.preview.total_credit}</span>
                      </div>
                    </div>

                    <div className="space-y-4">
                      {previewData.preview.proposed_entries.map(
                        (entry: BatchPreviewEntry, idx: number) => (
                          <Card key={idx}>
                            <CardHeader className="py-3">
                              <div className="flex items-center justify-between">
                                <CardTitle className="text-sm">
                                  {entry.date} - {entry.memo}
                                </CardTitle>
                              </div>
                            </CardHeader>
                            <CardContent className="py-0 pb-3">
                              <Table>
                                <TableHeader>
                                  <TableRow>
                                    <TableHead>{t("edim:batch.account", "Account")}</TableHead>
                                    <TableHead>{t("edim:batch.description", "Description")}</TableHead>
                                    <TableHead className="text-right">
                                      {t("edim:batch.debit", "Debit")}
                                    </TableHead>
                                    <TableHead className="text-right">
                                      {t("edim:batch.credit", "Credit")}
                                    </TableHead>
                                  </TableRow>
                                </TableHeader>
                                <TableBody>
                                  {entry.lines.map((line, lineIdx) => (
                                    <TableRow key={lineIdx}>
                                      <TableCell className="font-mono">
                                        {line.account_code}
                                      </TableCell>
                                      <TableCell>{line.description}</TableCell>
                                      <TableCell className="text-right">
                                        {line.debit !== "0.00" ? line.debit : ""}
                                      </TableCell>
                                      <TableCell className="text-right">
                                        {line.credit !== "0.00" ? line.credit : ""}
                                      </TableCell>
                                    </TableRow>
                                  ))}
                                </TableBody>
                              </Table>
                            </CardContent>
                          </Card>
                        )
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="text-center py-8 text-muted-foreground">
                    {t("edim:batch.noPreview", "No preview data available")}
                  </div>
                )}
              </TabsContent>
            </CardContent>
          </Tabs>
        </Card>

        {/* Map Dialog */}
        <Dialog open={mapDialogOpen} onOpenChange={setMapDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("edim:batch.mapTitle", "Apply Mapping Profile")}</DialogTitle>
              <DialogDescription>
                {t("edim:batch.mapDesc", "Select a mapping profile to transform the raw data")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="mappingProfile">
                  {t("edim:batch.mappingProfile", "Mapping Profile")}
                </Label>
                <Select
                  value={selectedProfileId}
                  onValueChange={setSelectedProfileId}
                >
                  <SelectTrigger id="mappingProfile">
                    <SelectValue placeholder={t("edim:batch.selectProfile", "Select a profile")} />
                  </SelectTrigger>
                  <SelectContent>
                    {activeProfiles.map((profile: MappingProfile) => (
                      <SelectItem key={profile.id} value={profile.id.toString()}>
                        <span className="font-medium">{profile.name}</span>
                        <span className="text-muted-foreground ml-2">v{profile.version}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {activeProfiles.length === 0 && (
                  <p className="text-sm text-muted-foreground flex items-center gap-1">
                    <AlertCircle className="h-4 w-4" />
                    {t("edim:batch.noProfiles", "No active profiles available for this source system")}
                  </p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setMapDialogOpen(false)}>
                {t("common:cancel")}
              </Button>
              <Button
                onClick={handleMap}
                disabled={!selectedProfileId || mapBatch.isPending}
              >
                {mapBatch.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    {t("edim:batch.mapping", "Mapping...")}
                  </>
                ) : (
                  t("edim:batch.applyMapping", "Apply Mapping")
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Commit Dialog */}
        <Dialog open={commitDialogOpen} onOpenChange={setCommitDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("edim:batch.commitTitle", "Commit Batch")}</DialogTitle>
              <DialogDescription>
                {t("edim:batch.commitDesc", "This will create journal entries in the accounting system. This action cannot be undone.")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="bg-muted p-4 rounded-md space-y-2">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">
                    {t("edim:batch.recordsToCommit", "Records to commit")}
                  </span>
                  <span className="font-medium">{batch.validated_records}</span>
                </div>
                {previewData?.preview && (
                  <>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">
                        {t("edim:batch.entriesToCreate", "Journal entries to create")}
                      </span>
                      <span className="font-medium">{previewData.preview.total_entries}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">
                        {t("edim:batch.totalAmount", "Total amount")}
                      </span>
                      <span className="font-medium">{previewData.preview.total_debit}</span>
                    </div>
                  </>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setCommitDialogOpen(false)}>
                {t("common:cancel")}
              </Button>
              <Button onClick={handleCommit} disabled={commitBatch.isPending}>
                {commitBatch.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    {t("edim:batch.committing", "Committing...")}
                  </>
                ) : (
                  <>
                    <Check className="mr-2 h-4 w-4" />
                    {t("edim:batch.confirmCommit", "Confirm & Commit")}
                  </>
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Reject Dialog */}
        <Dialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("edim:batch.rejectTitle", "Reject Batch")}</DialogTitle>
              <DialogDescription>
                {t("edim:batch.rejectDesc", "This will permanently reject the batch. Records will not be processed.")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="rejectReason">
                  {t("edim:batch.rejectReason", "Reason (optional)")}
                </Label>
                <Textarea
                  id="rejectReason"
                  placeholder={t("edim:batch.rejectReasonPlaceholder", "Enter reason for rejection...")}
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  rows={3}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setRejectDialogOpen(false)}>
                {t("common:cancel")}
              </Button>
              <Button
                variant="destructive"
                onClick={handleReject}
                disabled={rejectBatch.isPending}
              >
                {rejectBatch.isPending
                  ? t("common:rejecting", "Rejecting...")
                  : t("common:reject", "Reject")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "edim"])),
    },
  };
};
