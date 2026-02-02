import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import { Plus, ChevronRight, ChevronDown, Pencil, BookOpen, Download, FileSpreadsheet, FileText } from "lucide-react";
import { useState } from "react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PageHeader, EmptyState, LoadingSpinner, StatusBadge } from "@/components/common";
import { useBilingualText } from "@/components/common/BilingualText";
import { useAccounts, buildAccountTree } from "@/queries/useAccounts";
import { exportService, ExportFormat } from "@/services/export.service";
import { useToast } from "@/components/ui/toaster";
import type { Account } from "@/types/account";
import { cn } from "@/lib/cn";

export default function ChartOfAccountsPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const getText = useBilingualText();
  const { toast } = useToast();
  const { data: accounts, isLoading } = useAccounts();
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [isExporting, setIsExporting] = useState(false);

  const handleExport = async (format: ExportFormat) => {
    setIsExporting(true);
    try {
      await exportService.exportAccounts({ format, include_balance: true });
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

  const accountTree = accounts ? buildAccountTree(accounts) : [];

  const toggleExpand = (code: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(code)) {
        next.delete(code);
      } else {
        next.add(code);
      }
      return next;
    });
  };

  const expandAll = () => {
    if (accounts) {
      setExpanded(new Set(accounts.map((a) => a.code)));
    }
  };

  const collapseAll = () => {
    setExpanded(new Set());
  };

  const filterAccounts = (accounts: Account[]): Account[] => {
    if (!search) return accounts;
    const searchLower = search.toLowerCase();
    return accounts.filter(
      (a) =>
        a.code.toLowerCase().includes(searchLower) ||
        a.name.toLowerCase().includes(searchLower) ||
        a.name_ar?.toLowerCase().includes(searchLower)
    );
  };

  const renderAccount = (account: Account, depth = 0) => {
    const hasChildren = account.children && account.children.length > 0;
    const isExpanded = expanded.has(account.code);

    return (
      <div key={account.code}>
        <Link
          href={`/accounting/chart-of-accounts/${account.code}`}
          className={cn(
            "flex items-center gap-2 rounded-lg border p-3 hover:bg-muted transition-colors",
            account.is_header && "bg-muted/50"
          )}
          style={{ marginInlineStart: depth * 24 }}
        >
          {hasChildren && (
            <button
              onClick={(e) => {
                e.preventDefault();
                toggleExpand(account.code);
              }}
              className="p-1 hover:bg-background rounded"
            >
              {isExpanded ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
            </button>
          )}
          {!hasChildren && <div className="w-6" />}

          <span className="font-mono text-sm ltr-code w-20">{account.code}</span>

          <span className="flex-1 font-medium">
            {getText(account.name, account.name_ar)}
          </span>

          <Badge variant="outline" className="text-xs">
            {t(`accounting:accountTypes.${account.account_type}`, account.account_type)}
          </Badge>

          {account.has_transactions && (
            <Badge variant="secondary" className="text-xs gap-1">
              <BookOpen className="h-3 w-3" />
              {t("accounting:chartOfAccounts.hasTransactions", "Has Txns")}
            </Badge>
          )}

          <StatusBadge status={account.status} />

          <Pencil className="h-4 w-4 text-muted-foreground" />
        </Link>

        {hasChildren && isExpanded && (
          <div className="mt-1 space-y-1">
            {account.children!.map((child) => renderAccount(child, depth + 1))}
          </div>
        )}
      </div>
    );
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("accounting:chartOfAccounts.title")}
          subtitle={t("accounting:chartOfAccounts.subtitle")}
          actions={
            <div className="flex items-center gap-2">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" disabled={isExporting || isLoading}>
                    <Download className="me-2 h-4 w-4" />
                    {isExporting ? t("common:exporting", "Exporting...") : t("common:export", "Export")}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => handleExport("xlsx")}>
                    <FileSpreadsheet className="me-2 h-4 w-4" />
                    {t("accounting:export.excel", "Excel (.xlsx)")}
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleExport("csv")}>
                    <FileText className="me-2 h-4 w-4" />
                    {t("accounting:export.csv", "CSV (.csv)")}
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleExport("txt")}>
                    <FileText className="me-2 h-4 w-4" />
                    {t("accounting:export.txt", "Text (.txt)")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <Link href="/accounting/chart-of-accounts/new">
                <Button>
                  <Plus className="me-2 h-4 w-4" />
                  {t("accounting:chartOfAccounts.createAccount")}
                </Button>
              </Link>
            </div>
          }
        />

        <Card>
          <CardContent className="pt-6">
            {/* Search and Controls */}
            <div className="flex flex-col sm:flex-row gap-4 mb-6">
              <Input
                placeholder={t("actions.search")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="max-w-sm"
              />
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={expandAll}>
                  Expand All
                </Button>
                <Button variant="outline" size="sm" onClick={collapseAll}>
                  Collapse All
                </Button>
              </div>
            </div>

            {/* Account List */}
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : accountTree.length === 0 ? (
              <EmptyState
                title={t("messages.noData")}
                description={t("accounting:chartOfAccounts.createAccount")}
                action={
                  <Link href="/accounting/chart-of-accounts/new">
                    <Button>
                      <Plus className="me-2 h-4 w-4" />
                      {t("accounting:chartOfAccounts.createAccount")}
                    </Button>
                  </Link>
                }
              />
            ) : (
              <div className="space-y-1">
                {search
                  ? filterAccounts(accounts || []).map((account) =>
                      renderAccount(account, 0)
                    )
                  : accountTree.map((account) => renderAccount(account, 0))}
              </div>
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
