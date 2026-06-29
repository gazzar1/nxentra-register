import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { PageHeader, EmptyState } from "@/components/common";
import { PaginatedTable, type PaginatedColumnDef } from "@/components/common";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { CompanyDateInput } from "@/components/ui/CompanyDateInput";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";
import { useAccountDrilldown } from "@/queries/useAccountDrilldown";
import type { DateFormat } from "@/components/ui/CompanyDateInput";
import type {
  AccountDrilldownParams,
  BalanceSide,
  DrilldownDimension,
  DrilldownRow,
} from "@/types/account-drilldown";

// Row enriched with a stable key (the same JE can span several lines).
type IndexedRow = DrilldownRow & { _idx: number };

function SideBadge({ side }: { side: BalanceSide }) {
  return (
    <Badge variant={side === "DEBIT" ? "info" : "secondary"} className="ms-2 text-[10px]">
      {side}
    </Badge>
  );
}

function DimensionChip({ dim }: { dim: DrilldownDimension }) {
  return (
    <Badge variant="outline" className="text-xs" title={`${dim.label}: ${dim.display}`}>
      {dim.label}: {dim.display}
    </Badge>
  );
}

export default function AccountDrilldownPage() {
  const { t } = useTranslation(["common", "accounting"]);
  const router = useRouter();
  const { code } = router.query;
  const accountCode = typeof code === "string" ? code : "";
  const { formatCurrency, formatDate, dateFormat } = useCompanyFormat();

  // ── Filters ───────────────────────────────────────────────────────────
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [dimensionType, setDimensionType] = useState("");
  const [dimensionValue, setDimensionValue] = useState("");
  const [sourceModule, setSourceModule] = useState("");
  const [postedOnly, setPostedOnly] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const resetPage = () => setPage(1);

  const params: AccountDrilldownParams = useMemo(() => {
    const p: AccountDrilldownParams = { page, page_size: pageSize, posted_only: postedOnly };
    if (dateFrom) p.date_from = dateFrom;
    if (dateTo) p.date_to = dateTo;
    if (dimensionType) p.dimension_type = dimensionType;
    if (dimensionValue) p.dimension_value = dimensionValue;
    if (sourceModule) p.source_module = sourceModule;
    return p;
  }, [page, pageSize, postedOnly, dateFrom, dateTo, dimensionType, dimensionValue, sourceModule]);

  const { data, isLoading, isError } = useAccountDrilldown(accountCode, params);

  const account = data?.account;
  const summary = data?.summary;
  const currency = account?.currency;

  const rows: IndexedRow[] = useMemo(
    () => (data?.rows || []).map((r, i) => ({ ...r, _idx: i })),
    [data?.rows]
  );

  const toggleExpanded = (idx: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });

  const columns: PaginatedColumnDef<IndexedRow>[] = [
    {
      key: "date",
      label: t("accounting:drilldown.date", "Date"),
      render: (r) => <span className="whitespace-nowrap">{formatDate(r.date)}</span>,
    },
    {
      key: "journal_entry_number",
      label: t("accounting:drilldown.jeNo", "JE No."),
      render: (r) => (
        <span className="font-mono text-xs ltr-code">{r.journal_entry_number || "—"}</span>
      ),
    },
    {
      key: "description",
      label: t("accounting:drilldown.description", "Description"),
      render: (r) => r.description || "—",
    },
    {
      key: "source_document",
      label: t("accounting:drilldown.sourceDoc", "Source Doc"),
      render: (r) => r.source_document || "—",
    },
    {
      key: "debit",
      label: t("accounting:drilldown.debit", "Debit"),
      className: "text-end",
      render: (r) => (r.debit !== "0.00" ? formatCurrency(r.debit, currency) : "—"),
    },
    {
      key: "credit",
      label: t("accounting:drilldown.credit", "Credit"),
      className: "text-end",
      render: (r) => (r.credit !== "0.00" ? formatCurrency(r.credit, currency) : "—"),
    },
    {
      key: "running_balance",
      label: t("accounting:drilldown.runningBalance", "Running Balance"),
      className: "text-end",
      render: (r) => (
        <span className="whitespace-nowrap font-medium">
          {formatCurrency(r.running_balance, currency)}
          <SideBadge side={r.running_balance_side} />
        </span>
      ),
    },
    {
      key: "dimensions",
      label: t("accounting:drilldown.dimensions", "Dimensions"),
      render: (r) => {
        if (r.dimensions.length === 0) return <span className="text-muted-foreground">—</span>;
        const isOpen = expanded.has(r._idx);
        const shown = isOpen ? r.dimensions : r.dimensions.slice(0, 2);
        const hidden = r.dimensions.length - shown.length;
        return (
          <div className="flex flex-wrap items-center gap-1">
            {shown.map((dim) => (
              <DimensionChip key={dim.type} dim={dim} />
            ))}
            {hidden > 0 && (
              <button
                type="button"
                onClick={() => toggleExpanded(r._idx)}
                className="text-xs text-muted-foreground hover:text-foreground underline"
              >
                +{hidden}
              </button>
            )}
            {isOpen && r.dimensions.length > 2 && (
              <button
                type="button"
                onClick={() => toggleExpanded(r._idx)}
                className="text-xs text-muted-foreground hover:text-foreground underline"
              >
                {t("common:actions.less", "less")}
              </button>
            )}
          </div>
        );
      },
    },
  ];

  const summaryCards: Array<{ label: string; value?: string; side?: BalanceSide }> = [
    {
      label: t("accounting:drilldown.opening", "Opening Balance"),
      value: summary?.opening_balance,
      side: summary?.opening_balance_side,
    },
    {
      label: t("accounting:drilldown.periodDebits", "Period Debits"),
      value: summary?.period_debits,
    },
    {
      label: t("accounting:drilldown.periodCredits", "Period Credits"),
      value: summary?.period_credits,
    },
    {
      label: t("accounting:drilldown.closing", "Closing Balance"),
      value: summary?.closing_balance,
      side: summary?.closing_balance_side,
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={
            account
              ? `${account.code} — ${account.name}`
              : t("accounting:drilldown.title", "Account Drilldown")
          }
          subtitle={
            account
              ? `${t(`accounting:accountTypes.${account.type}`, account.type)} · ${account.currency}`
              : accountCode
          }
          actions={
            <Link href="/accounting/chart-of-accounts">
              <Button variant="outline">
                <ArrowLeft className="me-2 h-4 w-4" />
                {t("common:actions.back", "Back")}
              </Button>
            </Link>
          }
        />

        {/* Filters */}
        <Card>
          <CardContent className="pt-6">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <Label>{t("accounting:drilldown.dateFrom", "Date From")}</Label>
                  {dateFrom && (
                    <button
                      type="button"
                      onClick={() => {
                        setDateFrom("");
                        resetPage();
                      }}
                      className="text-xs text-muted-foreground underline hover:text-foreground"
                    >
                      {t("common:actions.clear", "Clear")}
                    </button>
                  )}
                </div>
                <CompanyDateInput
                  value={dateFrom}
                  onChange={(v) => {
                    setDateFrom(v);
                    resetPage();
                  }}
                  dateFormat={dateFormat as DateFormat}
                />
              </div>
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <Label>{t("accounting:drilldown.dateTo", "Date To")}</Label>
                  {dateTo && (
                    <button
                      type="button"
                      onClick={() => {
                        setDateTo("");
                        resetPage();
                      }}
                      className="text-xs text-muted-foreground underline hover:text-foreground"
                    >
                      {t("common:actions.clear", "Clear")}
                    </button>
                  )}
                </div>
                <CompanyDateInput
                  value={dateTo}
                  onChange={(v) => {
                    setDateTo(v);
                    resetPage();
                  }}
                  dateFormat={dateFormat as DateFormat}
                />
              </div>
              <div className="space-y-1">
                <Label>{t("accounting:drilldown.sourceModule", "Source Module")}</Label>
                <Input
                  value={sourceModule}
                  placeholder={t("accounting:drilldown.optional", "Optional")}
                  onChange={(e) => {
                    setSourceModule(e.target.value);
                    resetPage();
                  }}
                />
              </div>
              <div className="space-y-1">
                <Label>{t("accounting:drilldown.dimensionType", "Dimension Type")}</Label>
                <Input
                  value={dimensionType}
                  placeholder={t("accounting:drilldown.optional", "Optional")}
                  onChange={(e) => {
                    setDimensionType(e.target.value);
                    resetPage();
                  }}
                />
              </div>
              <div className="space-y-1">
                <Label>{t("accounting:drilldown.dimensionValue", "Dimension Value")}</Label>
                <Input
                  value={dimensionValue}
                  placeholder={t("accounting:drilldown.optional", "Optional")}
                  onChange={(e) => {
                    setDimensionValue(e.target.value);
                    resetPage();
                  }}
                />
              </div>
              <div className="flex items-end gap-2 pb-1">
                <input
                  id="posted-only"
                  type="checkbox"
                  className="h-4 w-4 rounded border-input"
                  checked={postedOnly}
                  onChange={(e) => {
                    setPostedOnly(e.target.checked);
                    resetPage();
                  }}
                />
                <Label htmlFor="posted-only" className="font-normal">
                  {t("accounting:drilldown.postedOnly", "Posted only")}
                </Label>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Summary cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {summaryCards.map((card) => (
            <Card key={card.label}>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  {card.label}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-semibold">
                  {card.value !== undefined ? formatCurrency(card.value, currency) : "—"}
                  {card.side && card.value !== undefined && <SideBadge side={card.side} />}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Transactions */}
        <Card>
          <CardHeader>
            <CardTitle>{t("accounting:drilldown.transactions", "Transactions")}</CardTitle>
          </CardHeader>
          <CardContent>
            {isError ? (
              <div className="py-8 text-center text-sm text-destructive">
                {t("accounting:drilldown.error", "Failed to load account transactions. Please try again.")}
              </div>
            ) : (
              <PaginatedTable<IndexedRow>
                data={rows}
                columns={columns}
                keyExtractor={(r) => r._idx}
                page={data?.pagination.page ?? page}
                pageSize={data?.pagination.page_size ?? pageSize}
                totalCount={data?.pagination.count ?? 0}
                totalPages={data?.pagination.total_pages ?? 1}
                onPageChange={setPage}
                onPageSizeChange={setPageSize}
                isLoading={isLoading}
                emptyState={
                  <EmptyState
                    title={t("accounting:drilldown.emptyTitle", "No transactions")}
                    description={t(
                      "accounting:drilldown.empty",
                      "No journal lines found for this account in the selected period."
                    )}
                  />
                }
              />
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
