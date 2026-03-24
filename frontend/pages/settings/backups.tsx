import { useState, useRef } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import {
  Download,
  Upload,
  Trash2,
  Database,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  AlertTriangle,
  FileArchive,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import {
  useBackups,
  useCreateBackup,
  useRestoreBackup,
  useDeleteBackup,
} from "@/queries/useBackups";
import { backupService } from "@/services/backup.service";
import type { BackupRecord } from "@/services/backup.service";

function formatBytes(bytes: number | null): string {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return "-";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function StatusBadge({ status }: { status: BackupRecord["status"] }) {
  const config = {
    COMPLETED: { icon: CheckCircle2, color: "text-green-600 bg-green-50", label: "Completed" },
    FAILED: { icon: XCircle, color: "text-red-600 bg-red-50", label: "Failed" },
    IN_PROGRESS: { icon: Loader2, color: "text-blue-600 bg-blue-50", label: "In Progress" },
    PENDING: { icon: Clock, color: "text-yellow-600 bg-yellow-50", label: "Pending" },
  }[status];

  const Icon = config.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${config.color}`}>
      <Icon className={`h-3 w-3 ${status === "IN_PROGRESS" ? "animate-spin" : ""}`} />
      {config.label}
    </span>
  );
}

function TypeBadge({ type }: { type: BackupRecord["backup_type"] }) {
  if (type === "RESTORE") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium text-purple-600 bg-purple-50">
        <Upload className="h-3 w-3" /> Restore
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium text-blue-600 bg-blue-50">
      <Download className="h-3 w-3" /> Backup
    </span>
  );
}

export default function BackupsPage() {
  const { t } = useTranslation(["common", "settings"]);
  const { toast } = useToast();
  const { data: backups, isLoading } = useBackups();
  const createBackup = useCreateBackup();
  const restoreBackup = useRestoreBackup();
  const deleteBackup = useDeleteBackup();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);

  const handleCreateBackup = async () => {
    try {
      await createBackup.mutateAsync();
      toast({ title: "Backup created", description: "Your company data has been backed up successfully.", variant: "success" });
    } catch (error) {
      toast({ title: "Backup failed", description: getErrorMessage(error), variant: "destructive" });
    }
  };

  const handleDownload = async (publicId: string) => {
    try {
      await backupService.downloadBackup(publicId);
    } catch (error) {
      toast({ title: "Download failed", description: getErrorMessage(error), variant: "destructive" });
    }
  };

  const handleDelete = async (publicId: string) => {
    try {
      await deleteBackup.mutateAsync(publicId);
      toast({ title: "Backup deleted", variant: "success" });
    } catch (error) {
      toast({ title: "Delete failed", description: getErrorMessage(error), variant: "destructive" });
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith(".zip")) {
      toast({ title: "Invalid file", description: "Please select a .zip backup file.", variant: "destructive" });
      return;
    }
    if (file.size > 500 * 1024 * 1024) {
      toast({ title: "File too large", description: "Maximum file size is 500MB.", variant: "destructive" });
      return;
    }

    setRestoreFile(file);
    setShowConfirm(true);
  };

  const handleRestore = async () => {
    if (!restoreFile) return;
    setShowConfirm(false);

    try {
      const result = await restoreBackup.mutateAsync(restoreFile);
      toast({
        title: "Restore completed",
        description: `${result.stats.cleared} records cleared, data imported in ${result.stats.duration_seconds}s.`,
        variant: "success",
      });
    } catch (error) {
      toast({ title: "Restore failed", description: getErrorMessage(error), variant: "destructive" });
    } finally {
      setRestoreFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  if (isLoading) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center min-h-[400px]">
          <LoadingSpinner />
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="max-w-4xl mx-auto space-y-6">
        <PageHeader title="Backup & Restore" subtitle="Export and restore your company data" />

        {/* Create Backup */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="h-5 w-5" />
              Create Backup
            </CardTitle>
            <CardDescription>
              Export all company data including events, transactions, invoices, settings,
              and connector configurations into a downloadable ZIP archive.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              onClick={handleCreateBackup}
              disabled={createBackup.isPending}
            >
              {createBackup.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Creating Backup...
                </>
              ) : (
                <>
                  <Download className="h-4 w-4 mr-2" />
                  Create Backup
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        {/* Restore */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Upload className="h-5 w-5" />
              Restore from Backup
            </CardTitle>
            <CardDescription>
              Upload a backup ZIP file to restore company data. This will replace all
              existing data for this company.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-2 p-3 rounded-md bg-amber-50 text-amber-800 text-sm">
              <AlertTriangle className="h-4 w-4 flex-shrink-0" />
              <span>
                Restoring will <strong>replace all company data</strong>. This action cannot be undone.
                Create a backup first if you want to preserve the current state.
              </span>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept=".zip"
              onChange={handleFileSelect}
              className="hidden"
            />

            <Button
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
              disabled={restoreBackup.isPending}
            >
              {restoreBackup.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Restoring...
                </>
              ) : (
                <>
                  <Upload className="h-4 w-4 mr-2" />
                  Select Backup File
                </>
              )}
            </Button>

            {/* Confirmation Dialog (inline) */}
            {showConfirm && restoreFile && (
              <div className="p-4 rounded-md border border-red-200 bg-red-50 space-y-3">
                <p className="text-sm text-red-800 font-medium">
                  Confirm Restore
                </p>
                <p className="text-sm text-red-700">
                  You are about to restore from <strong>{restoreFile.name}</strong>{" "}
                  ({formatBytes(restoreFile.size)}). All existing company data will be replaced.
                </p>
                <div className="flex gap-2">
                  <Button variant="destructive" size="sm" onClick={handleRestore}>
                    Yes, Restore
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setShowConfirm(false);
                      setRestoreFile(null);
                      if (fileInputRef.current) fileInputRef.current.value = "";
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Backup History */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileArchive className="h-5 w-5" />
              Backup History
            </CardTitle>
          </CardHeader>
          <CardContent>
            {!backups || backups.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">
                No backups yet. Create your first backup above.
              </p>
            ) : (
              <div className="divide-y">
                {backups.map((backup) => (
                  <div key={backup.id} className="py-3 flex items-center justify-between">
                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="flex items-center gap-2">
                        <TypeBadge type={backup.backup_type} />
                        <StatusBadge status={backup.status} />
                        <span className="text-xs text-muted-foreground">
                          {new Date(backup.created_at).toLocaleString()}
                        </span>
                      </div>
                      <div className="flex items-center gap-4 text-xs text-muted-foreground">
                        <span>{formatBytes(backup.file_size_bytes)}</span>
                        <span>{backup.event_count} events</span>
                        <span>{formatDuration(backup.duration_seconds)}</span>
                        {backup.created_by && <span>by {backup.created_by}</span>}
                      </div>
                      {backup.status === "FAILED" && backup.error_message && (
                        <p className="text-xs text-red-600 truncate max-w-md">
                          {backup.error_message}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-1 ml-4">
                      {backup.has_file && backup.status === "COMPLETED" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDownload(backup.id)}
                          title="Download"
                        >
                          <Download className="h-4 w-4" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(backup.id)}
                        title="Delete"
                        className="text-red-500 hover:text-red-700"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale ?? "en", ["common", "settings"])),
  },
});
