import { useState } from "react";
import { useRouter } from "next/router";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { AppLayout } from "@/components/layout";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { LoadingSpinner } from "@/components/common";
import { useModules, useUpdateModules, type ModuleInfo } from "@/queries/useModules";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import { cn } from "@/lib/cn";
import {
  Warehouse,
  ShoppingCart,
  Truck,
  Stethoscope,
  Home,
  Check,
  ArrowRight,
  BookOpen,
} from "lucide-react";

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  Warehouse,
  ShoppingCart,
  Truck,
  Stethoscope,
  Home,
  BookOpen,
};

const MODULE_DESCRIPTIONS: Record<string, string> = {
  sales: "Create sales invoices and manage customer billing.",
  purchases: "Track purchase bills and vendor payments.",
  inventory: "Manage stock balances, adjustments, and ledger.",
  clinic: "Run a clinic with patients, doctors, visits, and billing.",
  properties: "Manage properties, leases, tenants, and rent collection.",
};

function getIcon(name: string, className: string) {
  const Icon = ICON_MAP[name];
  if (!Icon) return <BookOpen className={className} />;
  return <Icon className={className} />;
}

export default function OnboardingModulesPage() {
  const { t } = useTranslation("common");
  const router = useRouter();
  const { toast } = useToast();
  const { data: modules, isLoading } = useModules();
  const updateModules = useUpdateModules();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [isSaving, setIsSaving] = useState(false);

  const optionalModules = modules?.filter((m) => !m.is_core) ?? [];

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const markOnboardingDone = () => {
    // Server-side onboarding_completed flag is the authoritative source
  };

  const handleContinue = async () => {
    if (selected.size === 0) {
      // No modules selected — go straight to dashboard
      markOnboardingDone();
      router.push("/dashboard");
      return;
    }

    setIsSaving(true);
    try {
      await updateModules.mutateAsync(
        optionalModules.map((m) => ({
          key: m.key,
          is_enabled: selected.has(m.key),
        })),
      );
      markOnboardingDone();
      router.push("/dashboard");
    } catch (error) {
      toast({
        title: t("messages.error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
      setIsSaving(false);
    }
  };

  return (
    <AppLayout>
      <div className="mx-auto max-w-2xl py-8">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold">
            {t("onboarding.modulesTitle", "Choose Your Modules")}
          </h1>
          <p className="mt-2 text-muted-foreground">
            {t(
              "onboarding.modulesSubtitle",
              "Select the modules your business needs. Core accounting is always included. You can change this anytime in Settings.",
            )}
          </p>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : (
          <>
            <div className="grid gap-3">
              {optionalModules.map((mod) => {
                const isSelected = selected.has(mod.key);
                return (
                  <button
                    key={mod.key}
                    type="button"
                    onClick={() => toggle(mod.key)}
                    className={cn(
                      "flex items-center gap-4 rounded-xl border-2 p-4 text-start transition-all",
                      isSelected
                        ? "border-accent bg-accent/5"
                        : "border-border hover:border-muted-foreground/30",
                    )}
                  >
                    <div
                      className={cn(
                        "flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-lg",
                        isSelected
                          ? "bg-accent/10 text-accent"
                          : "bg-muted text-muted-foreground",
                      )}
                    >
                      {getIcon(mod.icon, "h-6 w-6")}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium">{mod.label}</div>
                      <p className="text-sm text-muted-foreground">
                        {t(
                          `onboarding.moduleDesc.${mod.key}`,
                          MODULE_DESCRIPTIONS[mod.key] || "",
                        )}
                      </p>
                    </div>
                    <div
                      className={cn(
                        "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full border-2 transition-colors",
                        isSelected
                          ? "border-accent bg-accent text-primary-foreground"
                          : "border-muted-foreground/30",
                      )}
                    >
                      {isSelected && <Check className="h-3.5 w-3.5" />}
                    </div>
                  </button>
                );
              })}
            </div>

            <div className="mt-8 flex flex-col items-center gap-3">
              <Button
                size="lg"
                onClick={handleContinue}
                disabled={isSaving}
                className="min-w-[200px]"
              >
                {isSaving ? (
                  t("actions.loading")
                ) : (
                  <>
                    {selected.size > 0
                      ? t("onboarding.enableAndContinue", "Enable & Continue")
                      : t("onboarding.skipForNow", "Skip for Now")}
                    <ArrowRight className="ms-2 h-4 w-4" />
                  </>
                )}
              </Button>
              <p className="text-xs text-muted-foreground">
                {t(
                  "onboarding.changeAnytime",
                  "You can enable or disable modules anytime from Settings > Modules.",
                )}
              </p>
            </div>
          </>
        )}
      </div>
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
