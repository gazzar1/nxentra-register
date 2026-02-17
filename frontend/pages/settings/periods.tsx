import { useEffect, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { Lock, Unlock, Star, CheckCircle, XCircle, AlertTriangle } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader, LoadingSpinner, ConfirmDialog } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import { useToast } from "@/components/ui/toaster";
import {
  periodsService,
  fiscalYearService,
  FiscalPeriod,
  FiscalPeriodConfig,
  FiscalYearStatus,
  CloseReadinessResult,
} from "@/services/periods.service";

function formatPeriodNumber(period: number, fiscalYear: number): string {
  if (period === 13) return `ADJ/${fiscalYear}`;
  return `${String(period).padStart(3, "0")}/${fiscalYear}`;
}

export default function PeriodsPage() {
  const { t } = useTranslation(["common", "settings"]);
  const { toast } = useToast();
  const { hasPermission } = useAuth();
  const [periods, setPeriods] = useState<FiscalPeriod[]>([]);
  const [config, setConfig] = useState<FiscalPeriodConfig | null>(null);
  const [fiscalYearStatus, setFiscalYearStatus] = useState<FiscalYearStatus | null>(null);
  const [loading, setLoading] = useState(true);

  // Configuration state
  const [periodCount, setPeriodCount] = useState<string>("13");
  const [configuring, setConfiguring] = useState(false);
  const [configConfirmOpen, setConfigConfirmOpen] = useState(false);

  // Range state
  const [openFrom, setOpenFrom] = useState<string>("1");
  const [openTo, setOpenTo] = useState<string>("12");
  const [settingRange, setSettingRange] = useState(false);

  // Current period state
  const [currentPeriod, setCurrentPeriod] = useState<string>("1");
  const [settingCurrent, setSettingCurrent] = useState(false);

  // Close/Open confirm
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false);
  const [actionPeriod, setActionPeriod] = useState<FiscalPeriod | null>(null);
  const [actionType, setActionType] = useState<"close" | "open">("close");
  const [actionLoading, setActionLoading] = useState(false);

  // Fiscal Year Close wizard state
  const [closeReadiness, setCloseReadiness] = useState<CloseReadinessResult | null>(null);
  const [checkingReadiness, setCheckingReadiness] = useState(false);
  const [yearCloseConfirmOpen, setYearCloseConfirmOpen] = useState(false);
  const [closingYear, setClosingYear] = useState(false);
  const [retainedEarningsCode, setRetainedEarningsCode] = useState("");
  const [reopenConfirmOpen, setReopenConfirmOpen] = useState(false);
  const [reopening, setReopening] = useState(false);
  const [reopenReason, setReopenReason] = useState("");

  const canConfigure = hasPermission("periods.configure");
  const canClose = hasPermission("periods.close");
  const canReopen = hasPermission("periods.reopen");

  const fiscalYear = config?.fiscal_year || new Date().getFullYear();

  const fetchPeriods = async () => {
    try {
      const { data } = await periodsService.list();
      setPeriods(data.periods || []);
      setConfig(data.config || null);
      setFiscalYearStatus(data.fiscal_year_status || null);
      if (data.config) {
        setPeriodCount(String(data.config.period_count));
        setOpenFrom(String(data.config.open_from_period || 1));
        setOpenTo(String(data.config.open_to_period || 12));
        setCurrentPeriod(String(data.config.current_period || 1));
      }
    } catch {
      toast({
        title: t("messages.error"),
        description: t("messages.loadError"),
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPeriods();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleConfigure = async () => {
    setConfiguring(true);
    try {
      await periodsService.configure(fiscalYear, Number(periodCount));
      toast({
        title: t("messages.success"),
        description: t("settings:periods.configuredSuccess", "Periods reconfigured successfully"),
        variant: "success",
      });
      setConfigConfirmOpen(false);
      fetchPeriods();
    } catch {
      toast({
        title: t("messages.error"),
        description: t("settings:periods.configuredError", "Failed to reconfigure periods"),
        variant: "destructive",
      });
    } finally {
      setConfiguring(false);
    }
  };

  const handleSetRange = async () => {
    setSettingRange(true);
    try {
      await periodsService.setRange(fiscalYear, Number(openFrom), Number(openTo));
      toast({
        title: t("messages.success"),
        description: t("settings:periods.rangeSuccess", "Open range updated successfully"),
        variant: "success",
      });
      fetchPeriods();
    } catch {
      toast({
        title: t("messages.error"),
        description: t("settings:periods.rangeError", "Failed to update open range"),
        variant: "destructive",
      });
    } finally {
      setSettingRange(false);
    }
  };

  const handleSetCurrent = async () => {
    setSettingCurrent(true);
    try {
      await periodsService.setCurrent(fiscalYear, Number(currentPeriod));
      toast({
        title: t("messages.success"),
        description: t("settings:periods.currentSuccess", "Current period updated"),
        variant: "success",
      });
      fetchPeriods();
    } catch {
      toast({
        title: t("messages.error"),
        description: t("settings:periods.currentError", "Failed to update current period"),
        variant: "destructive",
      });
    } finally {
      setSettingCurrent(false);
    }
  };

  const handlePeriodAction = (period: FiscalPeriod, type: "close" | "open") => {
    setActionPeriod(period);
    setActionType(type);
    setCloseConfirmOpen(true);
  };

  const handleConfirmAction = async () => {
    if (!actionPeriod) return;
    setActionLoading(true);
    try {
      if (actionType === "close") {
        await periodsService.close(actionPeriod.fiscal_year, actionPeriod.period);
      } else {
        await periodsService.open(actionPeriod.fiscal_year, actionPeriod.period);
      }
      toast({
        title: t("messages.success"),
        description:
          actionType === "close"
            ? t("settings:periods.closedSuccess", "Period closed successfully")
            : t("settings:periods.openedSuccess", "Period opened successfully"),
        variant: "success",
      });
      setCloseConfirmOpen(false);
      fetchPeriods();
    } catch {
      toast({
        title: t("messages.error"),
        description:
          actionType === "close"
            ? t("settings:periods.closedError", "Failed to close period")
            : t("settings:periods.openedError", "Failed to open period"),
        variant: "destructive",
      });
    } finally {
      setActionLoading(false);
    }
  };

  const [savingDates, setSavingDates] = useState<string | null>(null);

  const handleDateChange = async (
    p: FiscalPeriod,
    field: "start_date" | "end_date",
    value: string
  ) => {
    if (!value) return;
    const newStart = field === "start_date" ? value : p.start_date;
    const newEnd = field === "end_date" ? value : p.end_date;
    const key = `${p.fiscal_year}-${p.period}`;
    setSavingDates(key);
    try {
      await periodsService.updateDates(p.fiscal_year, p.period, newStart, newEnd);
      toast({
        title: t("messages.success"),
        description: t("messages.saved", "Saved successfully"),
        variant: "success",
      });
      fetchPeriods();
    } catch {
      toast({
        title: t("messages.error"),
        description: t("messages.error", "An error occurred"),
        variant: "destructive",
      });
    } finally {
      setSavingDates(null);
    }
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString();
  };

  // --- Fiscal Year Close Wizard ---
  const handleCheckReadiness = async () => {
    setCheckingReadiness(true);
    setCloseReadiness(null);
    try {
      const { data } = await fiscalYearService.checkCloseReadiness(fiscalYear);
      setCloseReadiness(data);
    } catch {
      toast({
        title: t("messages.error"),
        description: t("settings:periods.readinessCheckFailed", "Failed to check close readiness"),
        variant: "destructive",
      });
    } finally {
      setCheckingReadiness(false);
    }
  };

  const handleCloseYear = async () => {
    if (!retainedEarningsCode.trim()) {
      toast({
        title: t("messages.error"),
        description: t("settings:periods.retainedEarningsRequired", "Retained earnings account code is required"),
        variant: "destructive",
      });
      return;
    }
    setClosingYear(true);
    try {
      await fiscalYearService.close(fiscalYear, retainedEarningsCode.trim());
      toast({
        title: t("messages.success"),
        description: t("settings:periods.yearClosedSuccess", "Fiscal year closed successfully"),
        variant: "success",
      });
      setYearCloseConfirmOpen(false);
      setCloseReadiness(null);
      setRetainedEarningsCode("");
      fetchPeriods();
    } catch {
      toast({
        title: t("messages.error"),
        description: t("settings:periods.yearClosedError", "Failed to close fiscal year"),
        variant: "destructive",
      });
    } finally {
      setClosingYear(false);
    }
  };

  const handleReopenYear = async () => {
    if (!reopenReason.trim()) {
      toast({
        title: t("messages.error"),
        description: t("settings:periods.reopenReasonRequired", "A reason is required to reopen a fiscal year"),
        variant: "destructive",
      });
      return;
    }
    setReopening(true);
    try {
      await fiscalYearService.reopen(fiscalYear, reopenReason.trim());
      toast({
        title: t("messages.success"),
        description: t("settings:periods.yearReopenedSuccess", "Fiscal year reopened successfully"),
        variant: "success",
      });
      setReopenConfirmOpen(false);
      setReopenReason("");
      fetchPeriods();
    } catch {
      toast({
        title: t("messages.error"),
        description: t("settings:periods.yearReopenedError", "Failed to reopen fiscal year"),
        variant: "destructive",
      });
    } finally {
      setReopening(false);
    }
  };

  // Separate normal periods from adjustment period for display
  const normalPeriods = periods.filter((p) => p.period_type === "NORMAL");
  const adjustmentPeriod = periods.find((p) => p.period_type === "ADJUSTMENT");

  // Remediation hints for failed readiness checks
  const getRemediationHint = (checkName: string): string | null => {
    const lower = checkName.toLowerCase();
    if (lower.includes("already closed"))
      return "This year has already been closed. Use 'Reopen' if adjustments are needed.";
    if (lower.includes("normal periods"))
      return "Close all periods 1-12 above before running year-end close. Use the period close buttons in the table.";
    if (lower.includes("period 13"))
      return "Run 'Configure Periods' with 13 periods to create the adjustment period, then ensure it is open.";
    if (lower.includes("draft") || lower.includes("incomplete"))
      return "Post or delete all draft/incomplete journal entries dated within this fiscal year.";
    if (lower.includes("tie-out") || lower.includes("subledger"))
      return "AR/AP control account balances do not match subledger totals. Review recent postings or run a reconciliation report.";
    if (lower.includes("projection"))
      return "Projections are still processing. Wait a moment and check readiness again.";
    return null;
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("settings:periods.title", "Fiscal Periods")}
          subtitle={t("settings:periods.subtitle", "Manage accounting periods and year-end close")}
        />

        {loading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : (
          <>
            {/* Fiscal Year Status Banner */}
            {fiscalYearStatus && (
              <Card className={fiscalYearStatus.status === "CLOSED" ? "border-amber-300 bg-amber-50 dark:bg-amber-950/20" : ""}>
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <CardTitle className="text-lg">
                        {t("settings:periods.fiscalYear", "Fiscal Year")} {fiscalYearStatus.fiscal_year}
                      </CardTitle>
                      <CardDescription>
                        {fiscalYearStatus.status === "CLOSED"
                          ? t("settings:periods.yearClosedAt", "Closed on {{date}}", {
                              date: fiscalYearStatus.closed_at
                                ? new Date(fiscalYearStatus.closed_at).toLocaleDateString()
                                : "N/A",
                            })
                          : t("settings:periods.yearOpen", "Year is open for posting")}
                      </CardDescription>
                    </div>
                    <Badge variant={fiscalYearStatus.status === "OPEN" ? "success" : "secondary"}>
                      {fiscalYearStatus.status}
                    </Badge>
                  </div>
                </CardHeader>
                {canConfigure && (
                  <CardContent className="pt-0">
                    <div className="flex gap-2">
                      {fiscalYearStatus.status === "OPEN" && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={handleCheckReadiness}
                          disabled={checkingReadiness}
                        >
                          {checkingReadiness
                            ? t("actions.loading", "Loading...")
                            : t("settings:periods.checkReadiness", "Check Close Readiness")}
                        </Button>
                      )}
                      {fiscalYearStatus.status === "CLOSED" && canReopen && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setReopenConfirmOpen(true)}
                        >
                          <Unlock className="h-3 w-3 me-1" />
                          {t("settings:periods.reopenYear", "Reopen Year")}
                        </Button>
                      )}
                    </div>
                  </CardContent>
                )}
              </Card>
            )}

            {/* Close Readiness Results */}
            {closeReadiness && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg flex items-center gap-2">
                    {closeReadiness.is_ready ? (
                      <CheckCircle className="h-5 w-5 text-green-600" />
                    ) : (
                      <AlertTriangle className="h-5 w-5 text-amber-600" />
                    )}
                    {t("settings:periods.closeReadiness", "Year-End Close Readiness")}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2 mb-4">
                    {closeReadiness.checks.map((check, idx) => {
                      const remediation = !check.passed
                        ? getRemediationHint(check.check)
                        : null;
                      return (
                        <div key={idx} className="flex items-start gap-2 text-sm">
                          {check.passed ? (
                            <CheckCircle className="h-4 w-4 text-green-600 mt-0.5 shrink-0" />
                          ) : (
                            <XCircle className="h-4 w-4 text-red-600 mt-0.5 shrink-0" />
                          )}
                          <div>
                            <span className="font-medium">{check.check}</span>
                            {check.detail && (
                              <span className="text-muted-foreground ms-1">- {check.detail}</span>
                            )}
                            {remediation && (
                              <p className="text-xs text-blue-600 mt-0.5">{remediation}</p>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  {closeReadiness.is_ready && (
                    <div className="space-y-3 border-t pt-3">
                      <div className="flex items-center gap-3">
                        <Label className="shrink-0">
                          {t("settings:periods.retainedEarningsAccount", "Retained Earnings Account Code")}
                        </Label>
                        <Input
                          value={retainedEarningsCode}
                          onChange={(e) => setRetainedEarningsCode(e.target.value)}
                          placeholder="3200"
                          className="w-32"
                        />
                      </div>
                      <Button
                        onClick={() => setYearCloseConfirmOpen(true)}
                        disabled={!retainedEarningsCode.trim()}
                      >
                        <Lock className="h-4 w-4 me-1" />
                        {t("settings:periods.closeYear", "Close Fiscal Year")}
                      </Button>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Configuration Card */}
            {canConfigure && (
              <Card>
                <CardHeader>
                  <CardTitle>{t("settings:periods.configuration", "Period Configuration")}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4 max-w-md">
                    {/* Current Period */}
                    <div className="flex items-center gap-4">
                      <Label className="w-40 shrink-0">
                        {t("settings:periods.currentPeriod", "Current Period")}
                      </Label>
                      <Input
                        type="number"
                        min={1}
                        max={12}
                        value={currentPeriod}
                        onChange={(e) => setCurrentPeriod(e.target.value)}
                        className="w-24"
                      />
                      <Button
                        onClick={handleSetCurrent}
                        disabled={settingCurrent || !currentPeriod}
                        size="sm"
                      >
                        {settingCurrent
                          ? t("actions.loading", "Loading...")
                          : t("actions.set", "Set")}
                      </Button>
                    </div>

                    {/* Open Periods Range */}
                    <div className="flex items-center gap-4">
                      <Label className="w-40 shrink-0">
                        {t("settings:periods.openPeriods", "Open Periods")}
                      </Label>
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-muted-foreground">
                          {t("date.from", "From")}
                        </span>
                        <Input
                          type="number"
                          min={1}
                          max={13}
                          value={openFrom}
                          onChange={(e) => setOpenFrom(e.target.value)}
                          className="w-20"
                        />
                        <span className="text-sm text-muted-foreground">
                          {t("date.to", "To")}
                        </span>
                        <Input
                          type="number"
                          min={1}
                          max={13}
                          value={openTo}
                          onChange={(e) => setOpenTo(e.target.value)}
                          className="w-20"
                        />
                        <Button
                          onClick={handleSetRange}
                          disabled={settingRange || !openFrom || !openTo}
                          size="sm"
                        >
                          {settingRange
                            ? t("actions.loading", "Loading...")
                            : t("actions.apply", "Apply")}
                        </Button>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Periods Table */}
            <Card>
              <CardHeader>
                <CardTitle>{t("settings:periods.fiscalPeriods", "Fiscal Periods")}</CardTitle>
              </CardHeader>
              <CardContent>
                {periods.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    {t("messages.noData", "No data available")}
                  </p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        {canConfigure && (
                          <TableHead className="w-12 text-center">
                            {t("settings:periods.current", "Current")}
                          </TableHead>
                        )}
                        <TableHead>{t("settings:periods.period", "Period")}</TableHead>
                        <TableHead>{t("settings:periods.type", "Type")}</TableHead>
                        <TableHead>{t("settings:periods.startDate", "Start Date")}</TableHead>
                        <TableHead>{t("settings:periods.endDate", "End Date")}</TableHead>
                        <TableHead>{t("settings:periods.status", "Status")}</TableHead>
                        {(canClose || canReopen) && (
                          <TableHead className="w-32"></TableHead>
                        )}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {/* Normal periods */}
                      {normalPeriods.map((p) => (
                        <TableRow
                          key={`${p.fiscal_year}-${p.period}`}
                          className={p.is_current ? "bg-muted/50" : ""}
                        >
                          {canConfigure && (
                            <TableCell className="text-center">
                              <input
                                type="radio"
                                name="current-period"
                                checked={p.is_current}
                                disabled={settingCurrent || p.period_type === "ADJUSTMENT"}
                                onChange={() => {
                                  if (p.is_current) return;
                                  setSettingCurrent(true);
                                  periodsService
                                    .setCurrent(p.fiscal_year, p.period)
                                    .then(() => {
                                      toast({
                                        title: t("messages.success"),
                                        description: t("settings:periods.currentSuccess", "Current period updated"),
                                        variant: "success",
                                      });
                                      fetchPeriods();
                                    })
                                    .catch(() => {
                                      toast({
                                        title: t("messages.error"),
                                        description: t("settings:periods.currentError", "Failed to update current period"),
                                        variant: "destructive",
                                      });
                                    })
                                    .finally(() => setSettingCurrent(false));
                                }}
                                className="h-4 w-4 cursor-pointer accent-blue-600"
                              />
                            </TableCell>
                          )}
                          <TableCell className="font-medium font-mono">
                            {formatPeriodNumber(p.period, p.fiscal_year)}
                            {!canConfigure && p.is_current && (
                              <Badge variant="outline" className="ms-2">
                                <Star className="h-3 w-3 me-1" />
                                {t("settings:periods.current", "Current")}
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline">{t("settings:periods.normal", "Normal")}</Badge>
                          </TableCell>
                          <TableCell>
                            {canConfigure ? (
                              <Input
                                type="date"
                                value={p.start_date}
                                onChange={(e) => handleDateChange(p, "start_date", e.target.value)}
                                disabled={savingDates === `${p.fiscal_year}-${p.period}`}
                                className="w-36 h-8 text-sm"
                              />
                            ) : (
                              formatDate(p.start_date)
                            )}
                          </TableCell>
                          <TableCell>
                            {canConfigure ? (
                              <Input
                                type="date"
                                value={p.end_date}
                                onChange={(e) => handleDateChange(p, "end_date", e.target.value)}
                                disabled={savingDates === `${p.fiscal_year}-${p.period}`}
                                className="w-36 h-8 text-sm"
                              />
                            ) : (
                              formatDate(p.end_date)
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant={p.status === "OPEN" ? "success" : "destructive"}
                            >
                              {p.status === "OPEN"
                                ? t("settings:periods.open", "Open")
                                : t("settings:periods.closed", "Closed")}
                            </Badge>
                          </TableCell>
                          {(canClose || canReopen) && (
                            <TableCell>
                              {p.status === "OPEN" && canClose && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handlePeriodAction(p, "close")}
                                >
                                  <Lock className="h-3 w-3 me-1" />
                                  {t("settings:periods.closePeriod", "Close")}
                                </Button>
                              )}
                              {p.status === "CLOSED" && canReopen && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handlePeriodAction(p, "open")}
                                >
                                  <Unlock className="h-3 w-3 me-1" />
                                  {t("settings:periods.openPeriod", "Open")}
                                </Button>
                              )}
                            </TableCell>
                          )}
                        </TableRow>
                      ))}

                      {/* Adjustment Period (Period 13) - visually separated */}
                      {adjustmentPeriod && (
                        <TableRow
                          className="border-t-2 border-dashed bg-amber-50/50 dark:bg-amber-950/10"
                        >
                          {canConfigure && (
                            <TableCell className="text-center">
                              {/* Cannot set P13 as current */}
                            </TableCell>
                          )}
                          <TableCell className="font-medium font-mono">
                            {formatPeriodNumber(adjustmentPeriod.period, adjustmentPeriod.fiscal_year)}
                          </TableCell>
                          <TableCell>
                            <Badge variant="secondary">
                              {t("settings:periods.adjustment", "Adjustment")}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-muted-foreground text-sm">
                            {formatDate(adjustmentPeriod.start_date)}
                          </TableCell>
                          <TableCell className="text-muted-foreground text-sm">
                            {formatDate(adjustmentPeriod.end_date)}
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant={adjustmentPeriod.status === "OPEN" ? "success" : "destructive"}
                            >
                              {adjustmentPeriod.status === "OPEN"
                                ? t("settings:periods.open", "Open")
                                : t("settings:periods.closed", "Closed")}
                            </Badge>
                          </TableCell>
                          {(canClose || canReopen) && (
                            <TableCell>
                              {adjustmentPeriod.status === "OPEN" && canClose && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handlePeriodAction(adjustmentPeriod, "close")}
                                >
                                  <Lock className="h-3 w-3 me-1" />
                                  {t("settings:periods.closePeriod", "Close")}
                                </Button>
                              )}
                              {adjustmentPeriod.status === "CLOSED" && canReopen && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handlePeriodAction(adjustmentPeriod, "open")}
                                >
                                  <Unlock className="h-3 w-3 me-1" />
                                  {t("settings:periods.openPeriod", "Open")}
                                </Button>
                              )}
                            </TableCell>
                          )}
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </>
        )}

        {/* Configure Confirm Dialog */}
        <ConfirmDialog
          open={configConfirmOpen}
          onOpenChange={setConfigConfirmOpen}
          title={t("settings:periods.configureConfirmTitle", "Reconfigure Periods")}
          description={t(
            "settings:periods.configureConfirmDescription",
            "This will delete all existing periods and create {{count}} new periods (12 normal + 1 adjustment). All period statuses will be reset to Open. Continue?",
            { count: parseInt(periodCount, 10) }
          )}
          confirmLabel={t("actions.apply", "Apply")}
          variant="destructive"
          onConfirm={handleConfigure}
          isLoading={configuring}
        />

        {/* Close/Open Period Confirm Dialog */}
        <ConfirmDialog
          open={closeConfirmOpen}
          onOpenChange={setCloseConfirmOpen}
          title={
            actionType === "close"
              ? t("settings:periods.closeConfirmTitle", "Close Fiscal Period")
              : t("settings:periods.openConfirmTitle", "Open Fiscal Period")
          }
          description={
            actionType === "close"
              ? t(
                  "settings:periods.closeConfirmDescription",
                  "Are you sure you want to close period {{period}}? No journal entries can be posted to a closed period.",
                  { period: actionPeriod ? formatPeriodNumber(actionPeriod.period, actionPeriod.fiscal_year) : "" }
                )
              : t(
                  "settings:periods.openConfirmDescription",
                  "Are you sure you want to reopen period {{period}}?",
                  { period: actionPeriod ? formatPeriodNumber(actionPeriod.period, actionPeriod.fiscal_year) : "" }
                )
          }
          confirmLabel={
            actionType === "close"
              ? t("settings:periods.closePeriod", "Close")
              : t("settings:periods.openPeriod", "Open")
          }
          variant={actionType === "close" ? "destructive" : "default"}
          onConfirm={handleConfirmAction}
          isLoading={actionLoading}
        />

        {/* Year-End Close Confirm Dialog */}
        <ConfirmDialog
          open={yearCloseConfirmOpen}
          onOpenChange={setYearCloseConfirmOpen}
          title={t("settings:periods.yearCloseConfirmTitle", "Close Fiscal Year")}
          description={t(
            "settings:periods.yearCloseConfirmDescription",
            "This will generate closing journal entries in Period 13 (ADJ), lock all periods for fiscal year {{year}}, and create next year's periods. This action can be reversed by reopening the year.",
            { year: fiscalYear }
          )}
          confirmLabel={t("settings:periods.closeYear", "Close Fiscal Year")}
          variant="destructive"
          onConfirm={handleCloseYear}
          isLoading={closingYear}
        />

        {/* Reopen Year Dialog */}
        <ConfirmDialog
          open={reopenConfirmOpen}
          onOpenChange={setReopenConfirmOpen}
          title={t("settings:periods.reopenYearTitle", "Reopen Fiscal Year")}
          description={t(
            "settings:periods.reopenYearDescription",
            "Reopening fiscal year {{year}} will reverse closing entries and reopen Period 13 (ADJ). A reason is required for audit trail.",
            { year: fiscalYear }
          )}
          confirmLabel={t("settings:periods.reopenYear", "Reopen Year")}
          variant="default"
          onConfirm={handleReopenYear}
          isLoading={reopening}
        >
          <div className="mt-3">
            <Label>{t("settings:periods.reopenReason", "Reason for Reopening")}</Label>
            <Input
              value={reopenReason}
              onChange={(e) => setReopenReason(e.target.value)}
              placeholder={t("settings:periods.reopenReasonPlaceholder", "e.g., Adjusting entry needed")}
              className="mt-1"
            />
          </div>
        </ConfirmDialog>
      </div>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "settings"])),
    },
  };
};
