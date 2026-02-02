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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
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
import type { JournalEntryStatus } from "@/types/journal";

export default function JournalEntriesPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { toast } = useToast();
  const { company } = useAuth();
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [isExporting, setIsExporting] = useState(false);

  const { data: entries, isLoading } = useJournalEntries(
    statusFilter !== "all" ? { status: statusFilter as JournalEntryStatus } : undefined
  );

  const handleExport = async (format: ExportFormat, detail: 'summary' | 'lines') => {
    setIsExporting(true);
    try {
      await exportService.exportJournalEntries({
        format,
        detail,
        status: statusFilter !== "all" ? statusFilter : undefined,
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

  const formatCurrency = (amount: string) => {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: company?.default_currency || "USD",
      minimumFractionDigits: 2,
    }).format(parseFloat(amount));
  };

  const formatDate = (date: string) => {
    return new Date(date).toLocaleDateString(router.locale === "ar" ? "ar-SA" : "en-US");
  };

  const filteredEntries = entries?.filter((entry) => {
    if (!search) return true;
    const searchLower = search.toLowerCase();
    return (
      entry.entry_number?.toLowerCase().includes(searchLower) ||
      entry.memo?.toLowerCase().includes(searchLower) ||
      entry.date.includes(search)
    );
  });

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
            {/* Filters */}
            <div className="flex flex-col sm:flex-row gap-4 mb-6">
              <Input
                placeholder={t("actions.search")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="max-w-sm"
              />
              <Select value={statusFilter} onValueChange={setStatusFilter}>
                <SelectTrigger className="w-40">
                  <SelectValue placeholder={t("accounting:journalEntry.status")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All</SelectItem>
                  <SelectItem value="INCOMPLETE">{t("status.incomplete")}</SelectItem>
                  <SelectItem value="DRAFT">{t("status.draft")}</SelectItem>
                  <SelectItem value="POSTED">{t("status.posted")}</SelectItem>
                  <SelectItem value="REVERSED">{t("status.reversed")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Table */}
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : !filteredEntries || filteredEntries.length === 0 ? (
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
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("accounting:journalEntry.entryNumber")}</TableHead>
                    <TableHead>{t("accounting:journalEntry.date")}</TableHead>
                    <TableHead>{t("accounting:journalEntry.memo")}</TableHead>
                    <TableHead className="text-end">{t("accounting:totals.totalDebit")}</TableHead>
                    <TableHead className="text-end">{t("accounting:totals.totalCredit")}</TableHead>
                    <TableHead>{t("accounting:journalEntry.status")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredEntries.map((entry) => (
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
                        {formatCurrency(entry.total_debit)}
                      </TableCell>
                      <TableCell className="text-end ltr-number">
                        {formatCurrency(entry.total_credit)}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={entry.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
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
