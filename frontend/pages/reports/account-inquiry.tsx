import { useState, useMemo } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { useRouter } from "next/router";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Printer, Search, Filter, ChevronLeft, ChevronRight } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useBilingualText } from "@/components/common/BilingualText";
import { useAccounts, useDimensions, useDimensionValues } from "@/queries/useAccounts";
import {
  periodsService,
  accountInquiryService,
  type AccountInquiryFilters,
} from "@/services/periods.service";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/cn";
import { useCompanyFormat } from "@/hooks/useCompanyFormat";

export default function AccountInquiryPage() {
  const { t } = useTranslation(["common", "reports"]);
  const router = useRouter();
  const getText = useBilingualText();
  const { company } = useAuth();
  const { formatCurrency, formatAmount, formatDate } = useCompanyFormat();

  // Filter state
  const currentYear = new Date().getFullYear();
  const [filters, setFilters] = useState<AccountInquiryFilters>({
    entry_type: "all",
    page: 1,
    page_size: 50,
  });
  const [appliedFilters, setAppliedFilters] = useState<AccountInquiryFilters>({
    entry_type: "all",
    page: 1,
    page_size: 50,
  });
  const [fiscalYear, setFiscalYear] = useState<number>(currentYear);
  const [selectedDimensionId, setSelectedDimensionId] = useState<number | null>(null);

  // Fetch accounts for dropdown
  const { data: accounts } = useAccounts();

  // Fetch dimensions for dropdown
  const { data: dimensions } = useDimensions();

  // Fetch dimension values when dimension is selected
  const { data: dimensionValues } = useDimensionValues(selectedDimensionId || 0);

  // Fetch periods for the selected fiscal year
  const { data: periodsData } = useQuery({
    queryKey: ["periods", fiscalYear],
    queryFn: async () => {
      const { data } = await periodsService.list(fiscalYear);
      return data;
    },
  });

  // Fiscal year options (last 5 years)
  const fiscalYearOptions = useMemo(() => {
    const years = [];
    for (let i = currentYear + 1; i >= currentYear - 4; i--) {
      years.push(i);
    }
    return years;
  }, [currentYear]);

  // Period options
  const periodOptions = useMemo(() => {
    if (!periodsData?.periods) return [];
    return periodsData.periods
      .filter((p) => p.fiscal_year === fiscalYear)
      .sort((a, b) => a.period - b.period);
  }, [periodsData, fiscalYear]);

  // Fetch inquiry data
  const { data: inquiryData, isLoading } = useQuery({
    queryKey: ["account-inquiry", appliedFilters],
    queryFn: async () => {
      const { data } = await accountInquiryService.query(appliedFilters);
      return data;
    },
    enabled: true,
  });

  const handleApplyFilters = () => {
    setAppliedFilters({ ...filters, page: 1 });
  };

  const handleClearFilters = () => {
    const cleared: AccountInquiryFilters = {
      entry_type: "all",
      page: 1,
      page_size: 50,
    };
    setFilters(cleared);
    setAppliedFilters(cleared);
    setSelectedDimensionId(null);
  };

  const handlePageChange = (newPage: number) => {
    setAppliedFilters({ ...appliedFilters, page: newPage });
  };

  const handlePrint = () => {
    window.print();
  };

  const updateFilter = <K extends keyof AccountInquiryFilters>(
    key: K,
    value: AccountInquiryFilters[K]
  ) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <AppLayout>
      <div className="space-y-6 print:space-y-0">
        <div className="no-print">
          <PageHeader
            title="Account Inquiry"
            subtitle="Search and filter journal entry lines"
            actions={
              <Button variant="outline" onClick={handlePrint}>
                <Printer className="me-2 h-4 w-4" />
                Print
              </Button>
            }
          />
        </div>

        {/* Filters Card */}
        <Card className="no-print">
          <CardContent className="pt-6">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {/* Account */}
              <div className="space-y-2">
                <Label>Account</Label>
                <Select
                  value={filters.account_code || "__all__"}
                  onValueChange={(v) => updateFilter("account_code", v === "__all__" ? undefined : v)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="All accounts" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__all__">All accounts</SelectItem>
                    {accounts?.map((acc) => (
                      <SelectItem key={acc.code} value={acc.code}>
                        {acc.code} - {getText(acc.name, acc.name_ar)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Date From */}
              <div className="space-y-2">
                <Label>Date From</Label>
                <Input
                  type="date"
                  value={filters.date_from || ""}
                  onChange={(e) => updateFilter("date_from", e.target.value || undefined)}
                />
              </div>

              {/* Date To */}
              <div className="space-y-2">
                <Label>Date To</Label>
                <Input
                  type="date"
                  value={filters.date_to || ""}
                  onChange={(e) => updateFilter("date_to", e.target.value || undefined)}
                />
              </div>

              {/* Entry Type */}
              <div className="space-y-2">
                <Label>Entry Type</Label>
                <Select
                  value={filters.entry_type || "all"}
                  onValueChange={(v) =>
                    updateFilter("entry_type", v as "debit" | "credit" | "all")
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All</SelectItem>
                    <SelectItem value="debit">Debits Only</SelectItem>
                    <SelectItem value="credit">Credits Only</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Fiscal Year */}
              <div className="space-y-2">
                <Label>Fiscal Year</Label>
                <Select
                  value={fiscalYear.toString()}
                  onValueChange={(v) => {
                    setFiscalYear(parseInt(v, 10));
                    updateFilter("fiscal_year", parseInt(v, 10));
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {fiscalYearOptions.map((year) => (
                      <SelectItem key={year} value={year.toString()}>
                        {year}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Period From */}
              <div className="space-y-2">
                <Label>Period From</Label>
                <Select
                  value={filters.period_from?.toString() || "__all__"}
                  onValueChange={(v) =>
                    updateFilter("period_from", v === "__all__" ? undefined : parseInt(v, 10))
                  }
                  disabled={periodOptions.length === 0}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select period" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__all__">All periods</SelectItem>
                    {periodOptions.map((p) => (
                      <SelectItem key={p.period} value={p.period.toString()}>
                        Period {p.period}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Period To */}
              <div className="space-y-2">
                <Label>Period To</Label>
                <Select
                  value={filters.period_to?.toString() || "__all__"}
                  onValueChange={(v) =>
                    updateFilter("period_to", v === "__all__" ? undefined : parseInt(v, 10))
                  }
                  disabled={periodOptions.length === 0}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select period" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__all__">All periods</SelectItem>
                    {periodOptions
                      .filter(
                        (p) =>
                          !filters.period_from || p.period >= filters.period_from
                      )
                      .map((p) => (
                        <SelectItem key={p.period} value={p.period.toString()}>
                          Period {p.period}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Reference */}
              <div className="space-y-2">
                <Label>Reference</Label>
                <Input
                  placeholder="Search reference..."
                  value={filters.reference || ""}
                  onChange={(e) =>
                    updateFilter("reference", e.target.value || undefined)
                  }
                />
              </div>

              {/* Amount Min */}
              <div className="space-y-2">
                <Label>Amount Min</Label>
                <Input
                  type="number"
                  step="0.01"
                  placeholder="0.00"
                  value={filters.amount_min || ""}
                  onChange={(e) =>
                    updateFilter("amount_min", e.target.value || undefined)
                  }
                />
              </div>

              {/* Amount Max */}
              <div className="space-y-2">
                <Label>Amount Max</Label>
                <Input
                  type="number"
                  step="0.01"
                  placeholder="0.00"
                  value={filters.amount_max || ""}
                  onChange={(e) =>
                    updateFilter("amount_max", e.target.value || undefined)
                  }
                />
              </div>

              {/* Currency */}
              <div className="space-y-2">
                <Label>Currency</Label>
                <Input
                  placeholder="e.g., USD"
                  value={filters.currency || ""}
                  onChange={(e) =>
                    updateFilter("currency", e.target.value || undefined)
                  }
                />
              </div>

              {/* Dimension */}
              <div className="space-y-2">
                <Label>Analysis Dimension</Label>
                <Select
                  value={selectedDimensionId?.toString() || "__all__"}
                  onValueChange={(v) => {
                    const dimId = v === "__all__" ? null : parseInt(v, 10);
                    setSelectedDimensionId(dimId);
                    updateFilter("dimension_id", dimId || undefined);
                    updateFilter("dimension_value_id", undefined);
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select dimension" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__all__">All dimensions</SelectItem>
                    {dimensions?.map((dim) => (
                      <SelectItem key={dim.id} value={dim.id.toString()}>
                        {dim.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Dimension Value */}
              {selectedDimensionId && (
                <div className="space-y-2">
                  <Label>Dimension Value</Label>
                  <Select
                    value={filters.dimension_value_id?.toString() || "__all__"}
                    onValueChange={(v) =>
                      updateFilter(
                        "dimension_value_id",
                        v === "__all__" ? undefined : parseInt(v, 10)
                      )
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select value" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__all__">All values</SelectItem>
                      {dimensionValues?.map((val) => (
                        <SelectItem key={val.id} value={val.id.toString()}>
                          {val.code} - {val.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>

            {/* Apply / Clear buttons */}
            <div className="flex gap-2 mt-6">
              <Button onClick={handleApplyFilters}>
                <Filter className="me-2 h-4 w-4" />
                Apply Filters
              </Button>
              <Button variant="outline" onClick={handleClearFilters}>
                Clear
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Results Card */}
        <Card className="print:shadow-none print:border-0">
          <CardContent className="pt-6 print:p-0">
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : !inquiryData || inquiryData.lines.length === 0 ? (
              <EmptyState
                icon={<Search className="h-12 w-12" />}
                title="No results"
                description="No journal lines match your filter criteria."
              />
            ) : (
              <>
                {/* Report Header (for print) */}
                <div className="text-center mb-6 print:mb-8 hidden print:block">
                  <h2 className="text-xl font-bold">{company?.name}</h2>
                  <h3 className="text-lg mt-2">Account Inquiry</h3>
                  <p className="text-muted-foreground mt-1">
                    {formatDate(new Date().toISOString())}
                  </p>
                </div>

                {/* Results count */}
                <div className="flex justify-between items-center mb-4 no-print">
                  <p className="text-sm text-muted-foreground">
                    Showing {inquiryData.lines.length} of{" "}
                    {inquiryData.pagination.total_count} results
                  </p>
                </div>

                {/* Table */}
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-24">Date</TableHead>
                        <TableHead className="w-24">Entry #</TableHead>
                        <TableHead className="w-24">Account</TableHead>
                        <TableHead>Description</TableHead>
                        <TableHead>Reference</TableHead>
                        <TableHead className="text-end w-28">Debit</TableHead>
                        <TableHead className="text-end w-28">Credit</TableHead>
                        <TableHead className="w-20">Currency</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {inquiryData.lines.map((line) => (
                        <TableRow key={`${line.entry_id}-${line.line_no}`}>
                          <TableCell className="text-sm">
                            {formatDate(line.entry_date)}
                          </TableCell>
                          <TableCell>
                            <Link
                              href={`/accounting/journal-entries/${line.entry_id}`}
                              className="font-mono text-sm hover:underline hover:text-primary"
                            >
                              {line.entry_number || `#${line.entry_id}`}
                            </Link>
                          </TableCell>
                          <TableCell>
                            <span className="font-mono text-sm ltr-code">
                              {line.account_code}
                            </span>
                          </TableCell>
                          <TableCell className="max-w-xs truncate">
                            {line.description || getText(line.account_name, line.account_name_ar ?? undefined)}
                          </TableCell>
                          <TableCell className="max-w-xs truncate text-sm text-muted-foreground">
                            {line.entry_reference || line.entry_memo || "-"}
                          </TableCell>
                          <TableCell className="text-end ltr-number">
                            {parseFloat(line.debit) > 0
                              ? formatCurrency(line.debit)
                              : "-"}
                          </TableCell>
                          <TableCell className="text-end ltr-number">
                            {parseFloat(line.credit) > 0
                              ? formatCurrency(line.credit)
                              : "-"}
                          </TableCell>
                          <TableCell className="text-sm">
                            {line.currency || "-"}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                    <TableFooter>
                      <TableRow className="font-bold">
                        <TableCell colSpan={5}>Total</TableCell>
                        <TableCell className="text-end ltr-number">
                          {formatCurrency(inquiryData.totals.debit)}
                        </TableCell>
                        <TableCell className="text-end ltr-number">
                          {formatCurrency(inquiryData.totals.credit)}
                        </TableCell>
                        <TableCell></TableCell>
                      </TableRow>
                      <TableRow className="font-bold">
                        <TableCell colSpan={5}>Net</TableCell>
                        <TableCell
                          colSpan={2}
                          className={cn(
                            "text-end ltr-number",
                            parseFloat(inquiryData.totals.net) > 0
                              ? "text-green-600"
                              : parseFloat(inquiryData.totals.net) < 0
                              ? "text-red-600"
                              : ""
                          )}
                        >
                          {formatCurrency(inquiryData.totals.net)}
                        </TableCell>
                        <TableCell></TableCell>
                      </TableRow>
                    </TableFooter>
                  </Table>
                </div>

                {/* Pagination */}
                {inquiryData.pagination.total_pages > 1 && (
                  <div className="flex justify-center items-center gap-4 mt-6 no-print">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={inquiryData.pagination.page <= 1}
                      onClick={() =>
                        handlePageChange(inquiryData.pagination.page - 1)
                      }
                    >
                      <ChevronLeft className="h-4 w-4" />
                      Previous
                    </Button>
                    <span className="text-sm text-muted-foreground">
                      Page {inquiryData.pagination.page} of{" "}
                      {inquiryData.pagination.total_pages}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={
                        inquiryData.pagination.page >=
                        inquiryData.pagination.total_pages
                      }
                      onClick={() =>
                        handlePageChange(inquiryData.pagination.page + 1)
                      }
                    >
                      Next
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                )}
              </>
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
      ...(await serverSideTranslations(locale ?? "en", ["common", "reports"])),
    },
  };
};
