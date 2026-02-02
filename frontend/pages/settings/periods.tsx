import { useEffect, useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { Lock, Unlock, Star } from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
  FiscalPeriod,
  FiscalPeriodConfig,
} from "@/services/periods.service";

function formatPeriodNumber(period: number, fiscalYear: number): string {
  return `${String(period).padStart(3, "0")}/${fiscalYear}`;
}

export default function PeriodsPage() {
  const { t } = useTranslation(["common", "settings"]);
  const { toast } = useToast();
  const { hasPermission } = useAuth();
  const [periods, setPeriods] = useState<FiscalPeriod[]>([]);
  const [config, setConfig] = useState<FiscalPeriodConfig | null>(null);
  const [loading, setLoading] = useState(true);

  // Configuration state
  const [periodCount, setPeriodCount] = useState<string>("12");
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

  const canConfigure = hasPermission("periods.configure");
  const canClose = hasPermission("periods.close");
  const canReopen = hasPermission("periods.reopen");

  const fiscalYear = config?.fiscal_year || new Date().getFullYear();

  const fetchPeriods = async () => {
    try {
      const { data } = await periodsService.list();
      setPeriods(data.periods || []);
      setConfig(data.config || null);
      if (data.config) {
        setPeriodCount(String(data.config.period_count));
        setOpenFrom(String(data.config.open_from_period || 1));
        setOpenTo(String(data.config.open_to_period || data.config.period_count));
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

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("settings:periods.title", "Fiscal Periods")}
          subtitle={t("settings:periods.subtitle", "Manage accounting periods")}
        />

        {loading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : (
          <>
            {/* Configuration Card */}
            {canConfigure && (
              <Card>
                <CardHeader>
                  <CardTitle>{t("settings:periods.configuration", "Period Configuration")}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4 max-w-md">
                    {/* Number of Periods */}
                    <div className="flex items-center gap-4">
                      <Label className="w-40 shrink-0">
                        {t("settings:periods.periodCount", "Number of Periods")}
                      </Label>
                      <Input
                        type="number"
                        min={1}
                        max={999}
                        value={periodCount}
                        onChange={(e) => setPeriodCount(e.target.value)}
                        className="w-24"
                      />
                      <Button
                        onClick={() => setConfigConfirmOpen(true)}
                        disabled={
                          !periodCount ||
                          Number(periodCount) < 1 ||
                          Number(periodCount) === config?.period_count
                        }
                        size="sm"
                      >
                        {t("actions.apply", "Apply")}
                      </Button>
                    </div>

                    {/* Current Period */}
                    <div className="flex items-center gap-4">
                      <Label className="w-40 shrink-0">
                        {t("settings:periods.currentPeriod", "Current Period")}
                      </Label>
                      <Input
                        type="number"
                        min={1}
                        max={Number(periodCount) || 999}
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
                          max={Number(periodCount) || 999}
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
                          max={Number(periodCount) || 999}
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
                        <TableHead>{t("settings:periods.startDate", "Start Date")}</TableHead>
                        <TableHead>{t("settings:periods.endDate", "End Date")}</TableHead>
                        <TableHead>{t("settings:periods.status", "Status")}</TableHead>
                        {(canClose || canReopen) && (
                          <TableHead className="w-32"></TableHead>
                        )}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {periods.map((p) => (
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
                                disabled={settingCurrent}
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
            "This will delete all existing periods and create {{count}} new periods. All period statuses will be reset to Open. Continue?",
            { count: parseInt(periodCount, 10) }
          )}
          confirmLabel={t("actions.apply", "Apply")}
          variant="destructive"
          onConfirm={handleConfigure}
          isLoading={configuring}
        />

        {/* Close/Open Confirm Dialog */}
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
