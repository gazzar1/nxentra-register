import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import { AppLayout } from "@/components/layout/AppLayout";
import { PageHeader } from "@/components/common/PageHeader";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
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
import { useToast } from "@/components/ui/toaster";
import { useSourceSystems, useMappingProfiles, useUploadBatch } from "@/queries/useEdim";
import { Upload, FileUp, ArrowLeft, FileText, AlertCircle } from "lucide-react";
import { useState, useCallback, useRef } from "react";
import { cn } from "@/lib/cn";
import type { SourceSystem, MappingProfile } from "@/types/edim";

const ACCEPTED_FILE_TYPES = {
  "text/csv": [".csv"],
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/vnd.ms-excel": [".xls"],
  "application/json": [".json"],
};

const ACCEPTED_EXTENSIONS = [".csv", ".xlsx", ".xls", ".json"];

export default function UploadBatchPage() {
  const { t } = useTranslation(["common", "edim"]);
  const router = useRouter();
  const { toast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [selectedSourceSystemId, setSelectedSourceSystemId] = useState<string>("");
  const [selectedMappingProfileId, setSelectedMappingProfileId] = useState<string>("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const { data: sourceSystems, isLoading: loadingSourceSystems } = useSourceSystems();
  const { data: mappingProfiles, isLoading: loadingProfiles } = useMappingProfiles(
    selectedSourceSystemId ? { source_system: parseInt(selectedSourceSystemId) } : undefined
  );
  const uploadBatch = useUploadBatch();

  const activeSourceSystems = sourceSystems?.filter((ss: SourceSystem) => ss.is_active) || [];
  const activeProfiles = mappingProfiles?.filter((p: MappingProfile) => p.status === "ACTIVE") || [];

  const handleFileSelect = useCallback((file: File) => {
    const extension = `.${file.name.split(".").pop()?.toLowerCase()}`;
    if (!ACCEPTED_EXTENSIONS.includes(extension)) {
      toast({
        title: t("common:error"),
        description: t("edim:upload.invalidFileType", "Invalid file type. Accepted: CSV, XLSX, XLS, JSON"),
        variant: "destructive",
      });
      return;
    }
    setSelectedFile(file);
  }, [toast, t]);

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) {
        handleFileSelect(file);
      }
    },
    [handleFileSelect]
  );

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        handleFileSelect(file);
      }
    },
    [handleFileSelect]
  );

  const handleUpload = async () => {
    if (!selectedSourceSystemId || !selectedFile) {
      toast({
        title: t("common:error"),
        description: t("edim:upload.missingFields", "Please select a source system and file"),
        variant: "destructive",
      });
      return;
    }

    try {
      const result = await uploadBatch.mutateAsync({
        sourceSystemId: parseInt(selectedSourceSystemId),
        file: selectedFile,
        mappingProfileId: selectedMappingProfileId ? parseInt(selectedMappingProfileId) : undefined,
      });

      toast({
        title: t("common:success"),
        description: t("edim:upload.success", "File uploaded successfully"),
      });

      // Navigate to the batch detail page
      const batchId = result.data?.id;
      if (batchId) {
        router.push(`/accounting/import/${batchId}`);
      } else {
        router.push("/accounting/import");
      }
    } catch {
      toast({
        title: t("common:error"),
        description: t("edim:upload.error", "Failed to upload file"),
        variant: "destructive",
      });
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const canUpload = selectedSourceSystemId && selectedFile && !uploadBatch.isPending;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("edim:upload.title", "Upload Data")}
          subtitle={t("edim:upload.subtitle", "Upload external data for import into the accounting system")}
          actions={
            <Button variant="outline" asChild>
              <Link href="/accounting/import">
                <ArrowLeft className="mr-2 h-4 w-4" />
                {t("common:back")}
              </Link>
            </Button>
          }
        />

        <div className="grid gap-6 lg:grid-cols-2">
          {/* Configuration */}
          <Card>
            <CardHeader>
              <CardTitle>{t("edim:upload.configTitle", "Configuration")}</CardTitle>
              <CardDescription>
                {t("edim:upload.configDesc", "Select the source system and optionally a mapping profile")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="sourceSystem">
                  {t("edim:upload.sourceSystem", "Source System")} *
                </Label>
                <Select
                  value={selectedSourceSystemId}
                  onValueChange={(value) => {
                    setSelectedSourceSystemId(value);
                    setSelectedMappingProfileId(""); // Reset profile when source changes
                  }}
                  disabled={loadingSourceSystems}
                >
                  <SelectTrigger id="sourceSystem">
                    <SelectValue placeholder={t("edim:upload.selectSourceSystem", "Select a source system")} />
                  </SelectTrigger>
                  <SelectContent>
                    {activeSourceSystems.map((ss: SourceSystem) => (
                      <SelectItem key={ss.id} value={ss.id.toString()}>
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{ss.name}</span>
                          <span className="text-muted-foreground">({ss.code})</span>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {activeSourceSystems.length === 0 && !loadingSourceSystems && (
                  <p className="text-sm text-muted-foreground flex items-center gap-1">
                    <AlertCircle className="h-4 w-4" />
                    {t("edim:upload.noSourceSystems", "No active source systems. Create one in Settings > Integrations.")}
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="mappingProfile">
                  {t("edim:upload.mappingProfile", "Mapping Profile")}
                  <span className="text-muted-foreground ml-1">
                    ({t("common:optional", "optional")})
                  </span>
                </Label>
                <Select
                  value={selectedMappingProfileId}
                  onValueChange={setSelectedMappingProfileId}
                  disabled={!selectedSourceSystemId || loadingProfiles}
                >
                  <SelectTrigger id="mappingProfile">
                    <SelectValue placeholder={t("edim:upload.selectMappingProfile", "Select a mapping profile")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">
                      {t("edim:upload.noProfile", "None (select later)")}
                    </SelectItem>
                    {activeProfiles.map((profile: MappingProfile) => (
                      <SelectItem key={profile.id} value={profile.id.toString()}>
                        <div>
                          <span className="font-medium">{profile.name}</span>
                          <span className="text-muted-foreground ml-2">v{profile.version}</span>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedSourceSystemId && activeProfiles.length === 0 && !loadingProfiles && (
                  <p className="text-sm text-muted-foreground">
                    {t("edim:upload.noProfiles", "No active profiles for this source system")}
                  </p>
                )}
              </div>
            </CardContent>
          </Card>

          {/* File Upload */}
          <Card>
            <CardHeader>
              <CardTitle>{t("edim:upload.fileTitle", "File")}</CardTitle>
              <CardDescription>
                {t("edim:upload.fileDesc", "Upload a CSV, Excel, or JSON file")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div
                className={cn(
                  "relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors",
                  isDragging
                    ? "border-primary bg-primary/5"
                    : "border-muted-foreground/25 hover:border-muted-foreground/50",
                  selectedFile && "border-primary bg-primary/5"
                )}
                onDrop={handleDrop}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPTED_EXTENSIONS.join(",")}
                  onChange={handleFileInputChange}
                  className="sr-only"
                  id="fileInput"
                />

                {selectedFile ? (
                  <div className="flex flex-col items-center gap-3 text-center">
                    <FileText className="h-12 w-12 text-primary" />
                    <div>
                      <p className="font-medium">{selectedFile.name}</p>
                      <p className="text-sm text-muted-foreground">
                        {formatFileSize(selectedFile.size)}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setSelectedFile(null);
                        if (fileInputRef.current) {
                          fileInputRef.current.value = "";
                        }
                      }}
                    >
                      {t("edim:upload.removeFile", "Remove")}
                    </Button>
                  </div>
                ) : (
                  <label
                    htmlFor="fileInput"
                    className="flex flex-col items-center gap-3 text-center cursor-pointer"
                  >
                    <FileUp className="h-12 w-12 text-muted-foreground" />
                    <div>
                      <p className="font-medium">
                        {t("edim:upload.dropzone", "Drop a file here or click to browse")}
                      </p>
                      <p className="text-sm text-muted-foreground mt-1">
                        {t("edim:upload.acceptedFormats", "CSV, XLSX, XLS, or JSON files")}
                      </p>
                    </div>
                  </label>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Upload Button */}
        <div className="flex justify-end gap-4">
          <Button variant="outline" asChild>
            <Link href="/accounting/import">{t("common:cancel")}</Link>
          </Button>
          <Button onClick={handleUpload} disabled={!canUpload}>
            {uploadBatch.isPending ? (
              <>
                <Upload className="mr-2 h-4 w-4 animate-pulse" />
                {t("edim:upload.uploading", "Uploading...")}
              </>
            ) : (
              <>
                <Upload className="mr-2 h-4 w-4" />
                {t("edim:upload.uploadButton", "Upload & Stage")}
              </>
            )}
          </Button>
        </div>
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
