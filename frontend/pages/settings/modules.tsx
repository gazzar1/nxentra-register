import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { AppLayout } from "@/components/layout";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useModules, useUpdateModules, type ModuleInfo } from "@/queries/useModules";
import { useAuth } from "@/contexts/AuthContext";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import {
  BookOpen,
  BarChart3,
  Settings,
  Wrench,
  LayoutDashboard,
  Warehouse,
  ShoppingCart,
  Truck,
  Stethoscope,
  Home,
  Lock,
  AlertTriangle,
} from "lucide-react";

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  BookOpen,
  BarChart3,
  Settings,
  Wrench,
  LayoutDashboard,
  Warehouse,
  ShoppingCart,
  Truck,
  Stethoscope,
  Home,
};

const CATEGORY_LABELS: Record<string, string> = {
  core: "Core",
  horizontal: "Business",
  vertical: "Industry",
  interaction: "Tools",
};

const CATEGORY_ORDER = ["core", "horizontal", "vertical", "interaction"];

function getIcon(name: string, className: string) {
  const Icon = ICON_MAP[name];
  if (!Icon) return <BookOpen className={className} />;
  return <Icon className={className} />;
}

function ModuleCard({
  mod,
  isToggling,
  onToggle,
}: {
  mod: ModuleInfo;
  isToggling: boolean;
  onToggle: (key: string, enable: boolean) => void;
}) {
  const { t } = useTranslation("common");

  return (
    <div className="flex items-center justify-between rounded-lg border p-4">
      <div className="flex items-center gap-3">
        {getIcon(mod.icon, "h-8 w-8 text-muted-foreground")}
        <div>
          <div className="flex items-center gap-2">
            <span className="font-medium">{mod.label}</span>
            {mod.is_core && (
              <Badge variant="info" className="text-[10px] px-1.5 py-0">
                {t("modules.core", "Core")}
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            {t(`modules.desc.${mod.key}`, MODULE_DESCRIPTIONS[mod.key] || "")}
          </p>
        </div>
      </div>

      <div className="flex-shrink-0 ms-4">
        {mod.is_core ? (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Lock className="h-3.5 w-3.5" />
            <span>{t("modules.alwaysOn", "Always on")}</span>
          </div>
        ) : mod.is_enabled ? (
          <Button
            variant="outline"
            size="sm"
            disabled={isToggling}
            onClick={() => onToggle(mod.key, false)}
          >
            {isToggling ? t("actions.loading") : t("modules.disable", "Disable")}
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={isToggling}
            onClick={() => onToggle(mod.key, true)}
          >
            {isToggling ? t("actions.loading") : t("modules.enable", "Enable")}
          </Button>
        )}
      </div>
    </div>
  );
}

const MODULE_DESCRIPTIONS: Record<string, string> = {
  dashboard: "Overview of your business at a glance.",
  setup: "Chart of accounts, dimensions, items, tax codes, and integrations.",
  accounting: "Journal entries, scratchpad, and data import.",
  reports: "Trial balance, financial statements, and account inquiry.",
  settings: "Company settings, users, and audit log.",
  sales: "Sales invoices and customer billing.",
  purchases: "Purchase bills and vendor payments.",
  inventory: "Stock balances, ledger, adjustments, and opening balances.",
  clinic: "Patients, doctors, visits, invoices, and payments.",
  properties: "Properties, units, leases, tenants, and rent collection.",
};

export default function ModulesSettingsPage() {
  const { t } = useTranslation("common");
  const { toast } = useToast();
  const { membership } = useAuth();
  const { data: modules, isLoading } = useModules();
  const updateModules = useUpdateModules();
  const [togglingKey, setTogglingKey] = useState<string | null>(null);
  const [confirmDisable, setConfirmDisable] = useState<string | null>(null);

  const isOwnerOrAdmin =
    membership?.role === "OWNER" || membership?.role === "ADMIN";

  const handleToggle = async (key: string, enable: boolean) => {
    if (!enable) {
      // Show confirmation before disabling
      setConfirmDisable(key);
      return;
    }
    await doToggle(key, true);
  };

  const doToggle = async (key: string, enable: boolean) => {
    setTogglingKey(key);
    try {
      await updateModules.mutateAsync([{ key, is_enabled: enable }]);
      toast({
        title: t("messages.success"),
        description: enable
          ? t("modules.enabled", "Module enabled successfully.")
          : t("modules.disabled", "Module disabled. Data has been preserved."),
        variant: "success",
      });
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setTogglingKey(null);
      setConfirmDisable(null);
    }
  };

  const disablingModule = modules?.find((m) => m.key === confirmDisable);

  // Group modules by category
  const grouped = CATEGORY_ORDER.reduce(
    (acc, cat) => {
      const mods = modules?.filter((m) => m.category === cat) ?? [];
      if (mods.length > 0) acc.push({ category: cat, modules: mods });
      return acc;
    },
    [] as { category: string; modules: ModuleInfo[] }[],
  );

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title={t("modules.title", "Modules")}
          subtitle={t(
            "modules.subtitle",
            "Enable or disable optional modules for your company.",
          )}
        />

        {!isOwnerOrAdmin && (
          <Card>
            <CardContent className="flex items-center gap-3 py-4">
              <Lock className="h-5 w-5 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                {t(
                  "modules.noPermission",
                  "Only company owners and administrators can manage modules.",
                )}
              </p>
            </CardContent>
          </Card>
        )}

        {isLoading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : (
          <div className="space-y-8 max-w-2xl">
            {grouped.map(({ category, modules: mods }) => (
              <Card key={category}>
                <CardHeader>
                  <CardTitle className="text-base">
                    {t(
                      `modules.category.${category}`,
                      CATEGORY_LABELS[category] || category,
                    )}
                  </CardTitle>
                  {category === "core" && (
                    <CardDescription>
                      {t(
                        "modules.coreDescription",
                        "Core modules are always enabled and cannot be turned off.",
                      )}
                    </CardDescription>
                  )}
                </CardHeader>
                <CardContent className="space-y-3">
                  {mods.map((mod) => (
                    <ModuleCard
                      key={mod.key}
                      mod={mod}
                      isToggling={togglingKey === mod.key}
                      onToggle={isOwnerOrAdmin ? handleToggle : () => {}}
                    />
                  ))}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Disable confirmation dialog */}
      <AlertDialog
        open={!!confirmDisable}
        onOpenChange={(open) => !open && setConfirmDisable(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-yellow-500" />
              {t("modules.confirmDisableTitle", "Disable Module?")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t(
                "modules.confirmDisableDescription",
                'Disabling "{{module}}" will hide it from the sidebar and block API access. All existing data will be preserved and restored when re-enabled.',
                { module: disablingModule?.label ?? "" },
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("actions.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => confirmDisable && doToggle(confirmDisable, false)}
            >
              {t("modules.confirmDisable", "Disable")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
