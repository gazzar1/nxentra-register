import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { useState, useMemo } from "react";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, EmptyState, LoadingSpinner, StatusBadge } from "@/components/common";
import { useJournalEntries } from "@/queries/useJournalEntries";
import { useAuth } from "@/contexts/AuthContext";
import { exportService, ExportFormat } from "@/services/export.service";
import { useToast } from "@/components/ui/toaster";

export default function JournalEntriesPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { company } = useAuth();
  const [search, setSearch] = useState("");
  const [isExporting, setIsExporting] = useState(false);
  const [activeTab, setActiveTab] = useState("posted");

  const { data: entries, isLoading } = useJournalEntries();

  // Split entries into posted vs drafts/incomplete
  const { postedEntries, draftEntries } = useMemo(() => {
    if (!entries) return { postedEntries: [], draftEntries: [] };
    const posted = entries.filter(
      (e) => e.status === "POSTED" || e.status === "REVERSED"
    );
    const drafts = entries.filter(
      (e) => e.status === "DRAFT" || e.status === "INCOMPLETE"
    );
    return { postedEntries: posted, draftEntries: drafts };
  }, [entries]);

  const currentEntries = activeTab === "posted" ? postedEntries : draftEntries;

  const filteredEntries = currentEntries.filter((entry) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      entry.entry_number?.toLowerCase().includes(searchLower) ||
      entry.memo?.toLowerCase().includes(searchLower) ||
      entry.date.includes(search)
    );
  });

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

  const formatCurrency = (amount: string, entryCurrency?: string) => {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: entryCurrency || company?.default_currency || "USD",
      minimumFractionDigits: 2,
    }).format(parseFloat(amount));
  };

  const formatDate = (date: string) => {
    return new Date(date).toLocaleDateString(router.locale === "ar" ? "ar-SA" : "en-US");
  };

  const renderTable = (items: typeof filteredEntries, showStatus: boolean) => {
    if (isLoading) {
      return (
        <div className="flex justify-center py-12">
          <LoadingSpinner size="lg" />
        </div>
      );
    }

    if (!items || items.length === 0) {
      return (
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
      );
    }

    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("accounting:journalEntry.entryNumber")}</TableHead>
            <TableHead>{t("accounting:journalEntry.date")}</TableHead>
            <TableHead>{t("accounting:journalEntry.memo")}</TableHead>
            <TableHead className="text-end">{t("accounting:totals.totalDebit")}</TableHead>
            <TableHead className="text-end">{t("accounting:totals.totalCredit")}</TableHead>
            {showStatus && <TableHead>{t("accounting:journalEntry.status")}</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((entry) => (
            <TableRow
              key={entry.id}
              className="cursor-pointer"
              onClick={() => router.push(`/accounting/journal-entries/${entry.id}`)}
            >
              <TableCell className="font-mono ltr-code">
                {entry.entry_number || `#${entry.id}`}
              </TableCell>
              <TableCell>{formatDate(entry.date)}</TableCell>
              <TableCell className="max-w-xs truncate">
                {entry.memo || "-"}
              </TableCell>
              <TableCell className="text-end ltr-number">
                {formatCurrency(entry.total_debit, entry.currency)}
              </TableCell>
              <TableCell className="text-end ltr-number">
                {formatCurrency(entry.total_credit, entry.currency)}
              </TableCell>
              {showStatus && (
                <TableCell>
                  <StatusBadge status={entry.status} />
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  };

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

        <Card>
          <CardContent className="pt-6">
            <Tabs value={activeTab} onValueChange={setActiveTab}>
              <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4 mb-6">
                <TabsList>
                  <TabsTrigger value="posted">
                    {t("status.posted", "Posted")}
                    {postedEntries.length > 0 && (
                      <span className="ms-2 rounded-full bg-muted px-2 py-0.5 text-xs">
                        {postedEntries.length}
                      </span>
                    )}
                  </TabsTrigger>
                  <TabsTrigger value="drafts">
                    {t("accounting:journalEntries.drafts", "Drafts")}
                    {draftEntries.length > 0 && (
                      <span className="ms-2 rounded-full bg-muted px-2 py-0.5 text-xs">
                        {draftEntries.length}
                      </span>
                    )}
                  </TabsTrigger>
                </TabsList>
                <Input
                  placeholder={t("actions.search")}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="max-w-sm"
                />
              </div>

              <TabsContent value="posted">
                {renderTable(filteredEntries, false)}
              </TabsContent>
              <TabsContent value="drafts">
                {renderTable(filteredEntries, true)}
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
