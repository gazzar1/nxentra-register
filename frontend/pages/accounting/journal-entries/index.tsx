import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { useState } from "react";
import { Plus, Download, FileSpreadsheet, FileText, List, Rows } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PageHeader, EmptyState, StatusBadge } from "@/components/common";
import { ContextualHelp } from "@/components/common/ContextualHelp";
import { PaginatedTable } from "@/components/common/PaginatedTable";
import type { ColumnDef } from "@/components/common/PaginatedTable";
import { usePaginatedJournalEntries } from "@/queries/useJournalEntries";
import { useAuth } from "@/contexts/AuthContext";
import { exportService, ExportFormat } from "@/services/export.service";
import { useToast } from "@/components/ui/toaster";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import type { JournalEntry } from "@/types/journal";

export default function JournalEntriesPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { company } = useAuth();
  const { formatCurrency, formatDate } = useCompanyFormat();
  const [search, setSearch] = useState("");
  const [isExporting, setIsExporting] = useState(false);
  const [activeTab, setActiveTab] = useState("posted");

  // Pagination + sorting state
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState("-entry_number");

  const statusFilter = activeTab === "posted" ? "POSTED" : undefined;

  const { data: response, isLoading } = usePaginatedJournalEntries({
    status: statusFilter,
    search: search || undefined,
    page,
    page_size: pageSize,
    ordering,
  });

  const entries = response?.results || [];
  const totalCount = response?.count || 0;
  const totalPages = response?.total_pages || 1;

  // Reset page when tab or search changes
  const handleTabChange = (tab: string) => {
    setActiveTab(tab);
    setPage(1);
  };
  const handleSearchChange = (value: string) => {
    setSearch(value);
    setPage(1);
  };

  const handleExport = async (format: ExportFormat, detail: 'summary' | 'lines') => {
    setIsExporting(true);
    try {
      await exportService.exportJournalEntries({
        format,
        detail,
        status: activeTab === "posted" ? "POSTED" : undefined,
      });
      toast({
        title: t("common:success"),
        description: t("accounting:export.success", "Export completed successfully"),
      });
    } catch (error) {
      toast({
        title: t("common:error"),
        description: t("accounting:export.error", "Failed to export data"),
        variant: "destructive",
      });
    } finally {
      setIsExporting(false);
    }
  };

  const showStatus = activeTab !== "posted";

  const columns: ColumnDef<JournalEntry>[] = [
    {
      key: "entry_number",
      label: t("accounting:journalEntry.entryNumber"),
      sortable: true,
      render: (entry) => (
        <span className="font-mono ltr-code">
          {entry.entry_number || `#${entry.id}`}
        </span>
      ),
    },
    {
      key: "date",
      label: t("accounting:journalEntry.date"),
      sortable: true,
      render: (entry) => formatDate(entry.date),
    },
    {
      key: "memo",
      label: t("accounting:journalEntry.memo"),
      sortable: true,
      className: "max-w-xs truncate",
      render: (entry) => entry.memo || "-",
    },
    {
      key: "currency",
      label: t("accounting:journalEntry.currency"),
      sortable: true,
      className: "text-center",
      render: (entry) => {
        const funcCurrency = company?.functional_currency || company?.default_currency || "USD";
        if (entry.currency && entry.currency !== funcCurrency) {
          return (
            <span className="inline-flex items-center rounded-full bg-blue-500/10 px-2 py-0.5 text-xs font-medium text-blue-500">
              {entry.currency}
            </span>
          );
        }
        return <span className="text-xs text-muted-foreground">{entry.currency || "-"}</span>;
      },
    },
    {
      key: "total_debit",
      label: t("accounting:totals.totalDebit"),
      className: "text-end",
      render: (entry) => (
        <span className="ltr-number">{formatCurrency(entry.total_debit, entry.currency)}</span>
      ),
    },
    {
      key: "total_credit",
      label: t("accounting:totals.totalCredit"),
      className: "text-end",
      render: (entry) => (
        <span className="ltr-number">{formatCurrency(entry.total_credit, entry.currency)}</span>
      ),
    },
    ...(showStatus
      ? [
          {
            key: "status",
            label: t("accounting:journalEntry.status"),
            sortable: true,
            render: (entry: JournalEntry) => <StatusBadge status={entry.status} />,
          },
        ]
      : []),
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("accounting:journalEntries.title")}
          subtitle={t("accounting:journalEntries.subtitle")}
          actions={
            <div className="flex items-center gap-2">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" disabled={isExporting || isLoading}>
                    <Download className="me-2 h-4 w-4" />
                    {isExporting ? t("common:exporting", "Exporting...") : t("common:export", "Export")}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-56">
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger>
                      <List className="me-2 h-4 w-4" />
                      {t("accounting:export.summary", "Summary")}
                    </DropdownMenuSubTrigger>
                    <DropdownMenuSubContent>
                      <DropdownMenuItem onClick={() => handleExport("xlsx", "summary")}>
                        <FileSpreadsheet className="me-2 h-4 w-4" />
                        Excel (.xlsx)
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleExport("csv", "summary")}>
                        <FileText className="me-2 h-4 w-4" />
                        CSV (.csv)
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleExport("txt", "summary")}>
                        <FileText className="me-2 h-4 w-4" />
                        Text (.txt)
                      </DropdownMenuItem>
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger>
                      <Rows className="me-2 h-4 w-4" />
                      {t("accounting:export.detailed", "Detailed (Lines)")}
                    </DropdownMenuSubTrigger>
                    <DropdownMenuSubContent>
                      <DropdownMenuItem onClick={() => handleExport("xlsx", "lines")}>
                        <FileSpreadsheet className="me-2 h-4 w-4" />
                        Excel (.xlsx)
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleExport("csv", "lines")}>
                        <FileText className="me-2 h-4 w-4" />
                        CSV (.csv)
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleExport("txt", "lines")}>
                        <FileText className="me-2 h-4 w-4" />
                        Text (.txt)
                      </DropdownMenuItem>
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>
                </DropdownMenuContent>
              </DropdownMenu>
              <Link href="/accounting/journal-entries/new">
                <Button>
                  <Plus className="me-2 h-4 w-4" />
                  {t("accounting:journalEntries.createEntry")}
                </Button>
              </Link>
            </div>
          }
        />

        <ContextualHelp items={[
          { question: "What does INCOMPLETE mean?", answer: "An INCOMPLETE entry was created by an automated process (e.g., Shopify) but couldn't be posted — usually because the period is closed or an account mapping is inactive. Review the entry, fix the issue, then post it manually." },
          { question: "What's the difference between Draft and Posted?", answer: "Draft entries are saved but haven't affected your books yet. Once posted, the entry updates account balances and cannot be edited — only reversed." },
          { question: "How do I fix an error in a posted entry?", answer: "You cannot edit posted entries. Instead, reverse the entry (creates an offsetting entry), then create a new corrected entry." },
        ]} />

        <Card>
          <CardContent className="pt-6">
            <Tabs value={activeTab} onValueChange={handleTabChange}>
              <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4 mb-6">
                <TabsList>
                  <TabsTrigger value="posted">
                    {t("status.posted", "Posted")}
                  </TabsTrigger>
                  <TabsTrigger value="drafts">
                    {t("accounting:journalEntries.drafts", "Drafts")}
                  </TabsTrigger>
                </TabsList>
                <Input
                  placeholder={t("actions.search")}
                  value={search}
                  onChange={(e) => handleSearchChange(e.target.value)}
                  className="max-w-sm"
                />
              </div>

              <TabsContent value="posted">
                <PaginatedTable
                  data={entries}
                  columns={columns}
                  keyExtractor={(e) => e.id}
                  page={page}
                  pageSize={pageSize}
                  totalCount={totalCount}
                  totalPages={totalPages}
                  onPageChange={setPage}
                  onPageSizeChange={setPageSize}
                  ordering={ordering}
                  onOrderingChange={setOrdering}
                  onRowClick={(entry) => router.push(`/accounting/journal-entries/${entry.id}`)}
                  isLoading={isLoading}
                  emptyState={
                    <EmptyState
                      title={t("messages.noData")}
                      action={
                        <Link href="/accounting/journal-entries/new">
                          <Button>
                            <Plus className="me-2 h-4 w-4" />
                            {t("accounting:journalEntries.createEntry")}
                          </Button>
                        </Link>
                      }
                    />
                  }
                />
              </TabsContent>
              <TabsContent value="drafts">
                <PaginatedTable
                  data={entries}
                  columns={columns}
                  keyExtractor={(e) => e.id}
                  page={page}
                  pageSize={pageSize}
                  totalCount={totalCount}
                  totalPages={totalPages}
                  onPageChange={setPage}
                  onPageSizeChange={setPageSize}
                  ordering={ordering}
                  onOrderingChange={setOrdering}
                  onRowClick={(entry) => router.push(`/accounting/journal-entries/${entry.id}`)}
                  isLoading={isLoading}
                  emptyState={
                    <EmptyState
                      title={t("messages.noData")}
                      action={
                        <Link href="/accounting/journal-entries/new">
                          <Button>
                            <Plus className="me-2 h-4 w-4" />
                            {t("accounting:journalEntries.createEntry")}
                          </Button>
                        </Link>
                      }
                    />
                  }
                />
              </TabsContent>
            </Tabs>
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
