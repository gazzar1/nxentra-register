import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toaster";
import { useBatches, useRejectBatch } from "@/queries/useEdim";
import { Upload, Eye, XCircle, FileText, CheckCircle, AlertCircle } from "lucide-react";
import { useState } from "react";
import type { IngestionBatch, BatchStatus } from "@/types/edim";

const STATUS_BADGES: Record<BatchStatus, { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
  STAGED: { label: "Staged", variant: "secondary" },
  MAPPED: { label: "Mapped", variant: "secondary" },
  VALIDATED: { label: "Validated", variant: "outline" },
  PREVIEWED: { label: "Previewed", variant: "outline" },
  COMMITTED: { label: "Committed", variant: "default" },
  REJECTED: { label: "Rejected", variant: "destructive" },
};

const STATUS_TABS = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "COMMITTED", label: "Committed" },
  { value: "REJECTED", label: "Rejected" },
];

export default function ImportBatchesPage() {
  const { t } = useTranslation(["common", "edim"]);
  const router = useRouter();
  const { toast } = useToast();

  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [selectedBatch, setSelectedBatch] = useState<IngestionBatch | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  // Determine API filter based on tab
  const getApiFilter = () => {
    if (statusFilter === "all") return undefined;
    if (statusFilter === "pending") {
      // Pending includes STAGED, MAPPED, VALIDATED, PREVIEWED
      return undefined; // We'll filter client-side for pending
    }
    return { status: statusFilter };
  };

  const { data: batches, isLoading } = useBatches(getApiFilter());
  const rejectBatch = useRejectBatch();

  // Client-side filtering for special tabs
  const filteredBatches = batches?.filter((batch: IngestionBatch) => {
    if (statusFilter === "all") return true;
    if (statusFilter === "pending") {
      return ["STAGED", "MAPPED", "VALIDATED", "PREVIEWED"].includes(batch.status);
    }
    return batch.status === statusFilter;
  });

  const handleRejectBatch = async () => {
    if (!selectedBatch) return;

    try {
      await rejectBatch.mutateAsync({
        batchId: selectedBatch.id,
        reason: rejectReason || undefined,
      });
      toast({
        title: t("common:success"),
        description: t("edim:batches.rejectSuccess", "Batch rejected successfully"),
      });
      setRejectDialogOpen(false);
      setSelectedBatch(null);
      setRejectReason("");
    } catch {
      toast({
        title: t("common:error"),
        description: t("edim:batches.rejectError", "Failed to reject batch"),
        variant: "destructive",
      });
    }
  };

  const openRejectDialog = (batch: IngestionBatch) => {
    setSelectedBatch(batch);
    setRejectReason("");
    setRejectDialogOpen(true);
  };

  const formatFileSize = (bytes: number | null) => {
    if (!bytes) return "-";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleString();
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("edim:batches.title", "Data Import")}
          subtitle={t("edim:batches.subtitle", "Import external data into the accounting system")}
          actions={
            <Button asChild>
              <Link href="/accounting/import/upload">
                <Upload className="mr-2 h-4 w-4" />
                {t("edim:batches.uploadNew", "Upload File")}
              </Link>
            </Button>
          }
        />

        <Tabs value={statusFilter} onValueChange={setStatusFilter} className="w-full">
          <TabsList>
            {STATUS_TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {t(`edim:batches.statusTab.${tab.value}`, tab.label)}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value={statusFilter} className="mt-4">
            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <div className="text-muted-foreground">{t("common:loading")}</div>
              </div>
            ) : !filteredBatches?.length ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <FileText className="h-12 w-12 text-muted-foreground mb-4" />
                <h3 className="text-lg font-medium">
                  {t("edim:batches.noBatches", "No batches found")}
                </h3>
                <p className="text-muted-foreground mt-1">
                  {t("edim:batches.noBatchesDesc", "Upload a file to start importing data")}
                </p>
                <Button asChild className="mt-4">
                  <Link href="/accounting/import/upload">
                    <Upload className="mr-2 h-4 w-4" />
                    {t("edim:batches.uploadNew", "Upload File")}
                  </Link>
                </Button>
              </div>
            ) : (
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("edim:batches.filename", "Filename")}</TableHead>
                      <TableHead>{t("edim:batches.sourceSystem", "Source System")}</TableHead>
                      <TableHead>{t("edim:batches.status", "Status")}</TableHead>
                      <TableHead className="text-right">{t("edim:batches.records", "Records")}</TableHead>
                      <TableHead className="text-right">{t("edim:batches.errors", "Errors")}</TableHead>
                      <TableHead>{t("edim:batches.uploadedBy", "Uploaded By")}</TableHead>
                      <TableHead>{t("edim:batches.uploadedAt", "Uploaded At")}</TableHead>
                      <TableHead className="text-right">{t("common:actions")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredBatches.map((batch: IngestionBatch) => (
                      <TableRow key={batch.id}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <FileText className="h-4 w-4 text-muted-foreground" />
                            <div>
                              <div className="font-medium">{batch.original_filename}</div>
                              <div className="text-xs text-muted-foreground">
                                {formatFileSize(batch.file_size_bytes)}
                              </div>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          <div>
                            <div className="font-medium">{batch.source_system_name}</div>
                            <div className="text-xs text-muted-foreground">
                              {batch.source_system_code}
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <Badge variant={STATUS_BADGES[batch.status]?.variant || "secondary"}>
                              {STATUS_BADGES[batch.status]?.label || batch.status}
                            </Badge>
                            {batch.status === "COMMITTED" && (
                              <CheckCircle className="h-4 w-4 text-green-600" />
                            )}
                            {batch.error_count > 0 && batch.status !== "REJECTED" && (
                              <AlertCircle className="h-4 w-4 text-amber-500" />
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-right">
                          <div>
                            <div>{batch.total_records}</div>
                            {batch.validated_records > 0 && (
                              <div className="text-xs text-muted-foreground">
                                {batch.validated_records} valid
                              </div>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-right">
                          {batch.error_count > 0 ? (
                            <span className="text-destructive font-medium">{batch.error_count}</span>
                          ) : (
                            <span className="text-muted-foreground">0</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <div className="text-sm">{batch.staged_by_email || "-"}</div>
                        </TableCell>
                        <TableCell>
                          <div className="text-sm">{formatDate(batch.created_at)}</div>
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-2">
                            <Button
                              variant="ghost"
                              size="sm"
                              asChild
                            >
                              <Link href={`/accounting/import/${batch.id}`}>
                                <Eye className="h-4 w-4" />
                              </Link>
                            </Button>
                            {!["COMMITTED", "REJECTED"].includes(batch.status) && (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => openRejectDialog(batch)}
                              >
                                <XCircle className="h-4 w-4 text-destructive" />
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </TabsContent>
        </Tabs>

        {/* Reject Dialog */}
        <Dialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("edim:batches.rejectTitle", "Reject Batch")}</DialogTitle>
              <DialogDescription>
                {t("edim:batches.rejectDesc", "This will permanently reject the batch. Any staged records will not be processed.")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="rejectReason">
                  {t("edim:batches.rejectReason", "Reason (optional)")}
                </Label>
                <Textarea
                  id="rejectReason"
                  placeholder={t("edim:batches.rejectReasonPlaceholder", "Enter reason for rejection...")}
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
                onClick={handleRejectBatch}
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
