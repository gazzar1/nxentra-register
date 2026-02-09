import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useState, useMemo, useCallback } from "react";
import { v4 as uuidv4 } from "uuid";
import { AppLayout } from "@/components/layout";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { ScratchpadGrid, ScratchpadToolbar } from "@/components/scratchpad";
import {
  useScratchpadRows,
  useCreateScratchpadRow,
  useUpdateScratchpadRow,
  useDeleteScratchpadRow,
  useBulkDeleteScratchpadRows,
  useValidateScratchpadRows,
  useCommitScratchpadGroups,
  useImportScratchpad,
  useDimensionSchema,
} from "@/queries/useScratchpad";
import { useAccounts } from "@/queries/useAccounts";
import { scratchpadService } from "@/services/scratchpad.service";
import type { ScratchpadRowUpdatePayload, ScratchpadFilters } from "@/types/scratchpad";

export default function ScratchpadPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const { toast } = useToast();
  const [selectedRows, setSelectedRows] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("all");

  // Filters for the query
  const filters: ScratchpadFilters = useMemo(() => {
    if (statusFilter === "all") return {};
    return { status: statusFilter as any };
  }, [statusFilter]);

  // Queries
  const {
    data: rows = [],
    isLoading: isLoadingRows,
    refetch: refetchRows,
  } = useScratchpadRows(filters);
  const { data: accounts = [], isLoading: isLoadingAccounts } = useAccounts();
  const { data: dimensionSchema } = useDimensionSchema();

  // Count ready rows for commit button
  const readyCount = useMemo(
    () => rows.filter((r) => r.status === "READY").length,
    [rows]
  );

  // Mutations
  const createRow = useCreateScratchpadRow();
  const updateRow = useUpdateScratchpadRow();
  const deleteRow = useDeleteScratchpadRow();
  const bulkDelete = useBulkDeleteScratchpadRows();
  const validateRows = useValidateScratchpadRows();
  const commitGroups = useCommitScratchpadGroups();
  const importRows = useImportScratchpad();

  // Handlers
  const handleAddRow = useCallback(() => {
    const groupId = uuidv4();
    createRow.mutate(
      {
        group_id: groupId,
        source: "manual",
      },
      {
        onSuccess: () => {
          toast({
            title: "Row added",
            description: "A new scratchpad row has been created.",
          });
        },
        onError: (error) => {
          toast({
            title: "Error",
            description: "Failed to create row.",
            variant: "destructive",
          });
        },
      }
    );
  }, [createRow, toast]);

  const handleRowUpdate = useCallback(
    (publicId: string, data: ScratchpadRowUpdatePayload) => {
      updateRow.mutate(
        { publicId, data },
        {
          onError: () => {
            toast({
              title: "Error",
              description: "Failed to update row.",
              variant: "destructive",
            });
          },
        }
      );
    },
    [updateRow, toast]
  );

  const handleRowDelete = useCallback(
    (publicId: string) => {
      deleteRow.mutate(publicId, {
        onSuccess: () => {
          setSelectedRows((prev) => prev.filter((id) => id !== publicId));
        },
        onError: () => {
          toast({
            title: "Error",
            description: "Failed to delete row.",
            variant: "destructive",
          });
        },
      });
    },
    [deleteRow, toast]
  );

  const handleDeleteSelected = useCallback(() => {
    if (selectedRows.length === 0) return;

    bulkDelete.mutate(
      { public_ids: selectedRows },
      {
        onSuccess: (response) => {
          setSelectedRows([]);
          toast({
            title: "Rows deleted",
            description: `${response.data.deleted_count} row(s) deleted.`,
          });
        },
        onError: () => {
          toast({
            title: "Error",
            description: "Failed to delete rows.",
            variant: "destructive",
          });
        },
      }
    );
  }, [selectedRows, bulkDelete, toast]);

  const handleValidateSelected = useCallback(() => {
    if (selectedRows.length === 0) return;

    validateRows.mutate(
      { public_ids: selectedRows },
      {
        onSuccess: (response) => {
          const data = response.data;
          toast({
            title: "Validation complete",
            description: `${data.ready_count} ready, ${data.invalid_count} invalid.`,
          });
        },
        onError: () => {
          toast({
            title: "Error",
            description: "Failed to validate rows.",
            variant: "destructive",
          });
        },
      }
    );
  }, [selectedRows, validateRows, toast]);

  const handleCommitReady = useCallback(
    (postImmediately: boolean) => {
      // Get unique group_ids from ready rows
      const readyRows = rows.filter((r) => r.status === "READY");
      const groupIds = Array.from(new Set(readyRows.map((r) => r.group_id)));

      if (groupIds.length === 0) {
        toast({
          title: "No ready rows",
          description: "Validate rows before committing.",
          variant: "destructive",
        });
        return;
      }

      commitGroups.mutate(
        { group_ids: groupIds, post_immediately: postImmediately },
        {
          onSuccess: (response) => {
            const data = response.data;
            toast({
              title: "Commit successful",
              description: `Created ${data.journal_entries.length} journal entries.`,
            });
            setSelectedRows([]);
          },
          onError: () => {
            toast({
              title: "Error",
              description: "Failed to commit rows.",
              variant: "destructive",
            });
          },
        }
      );
    },
    [rows, commitGroups, toast]
  );

  const handleImport = useCallback(
    (file: File) => {
      importRows.mutate(
        { file },
        {
          onSuccess: (response) => {
            const data = response.data;
            toast({
              title: "Import complete",
              description: `Imported ${data.created.length} rows.`,
            });
          },
          onError: () => {
            toast({
              title: "Error",
              description: "Failed to import file.",
              variant: "destructive",
            });
          },
        }
      );
    },
    [importRows, toast]
  );

  const handleExport = useCallback(
    async (format: "csv" | "xlsx") => {
      try {
        const response = await scratchpadService.export(format, filters);
        // Create download link
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement("a");
        link.href = url;
        link.setAttribute(
          "download",
          `scratchpad_export.${format === "xlsx" ? "xlsx" : "csv"}`
        );
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);

        toast({
          title: "Export complete",
          description: "File downloaded successfully.",
        });
      } catch (error) {
        toast({
          title: "Error",
          description: "Failed to export data.",
          variant: "destructive",
        });
      }
    },
    [filters, toast]
  );

  const handleRefresh = useCallback(() => {
    refetchRows();
  }, [refetchRows]);

  const isLoading = isLoadingRows || isLoadingAccounts;

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Scratchpad"
          subtitle="Draft transactions before committing to the journal"
        />

        <Card>
          <ScratchpadToolbar
            selectedCount={selectedRows.length}
            readyCount={readyCount}
            onAddRow={handleAddRow}
            onDeleteSelected={handleDeleteSelected}
            onValidateSelected={handleValidateSelected}
            onCommitReady={handleCommitReady}
            onImport={handleImport}
            onExport={handleExport}
            onRefresh={handleRefresh}
            isValidating={validateRows.isPending}
            isCommitting={commitGroups.isPending}
            isImporting={importRows.isPending}
          />

          <CardContent className="p-0">
            <ScratchpadGrid
              rows={rows}
              accounts={accounts}
              dimensionSchema={dimensionSchema}
              onRowUpdate={handleRowUpdate}
              onRowDelete={handleRowDelete}
              selectedRows={selectedRows}
              onSelectionChange={setSelectedRows}
              isLoading={isLoading}
            />
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "accounting"])),
    },
  };
};
