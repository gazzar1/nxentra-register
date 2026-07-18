import { useState, useEffect } from "react";
import { useRouter } from "next/router";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { AppLayout } from "@/components/layout";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingSpinner } from "@/components/common";
import { useModules, type ModuleInfo } from "@/queries/useModules";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import { cn } from "@/lib/cn";
import {
  onboardingService,
  type OnboardingStatus,
  type CoaTemplate,
  type OnboardingSetupPayload,
} from "@/services/onboarding.service";
import { shopifyService } from "@/services/shopify.service";
import { isShopifyEmbedded, redirectTopLevel } from "@/lib/shopify-embed";
import {
  BookOpen,
  Puzzle,
  Rocket,
  Check,
  ArrowRight,
  ArrowLeft,
  ChevronDown,
  Warehouse,
  ShoppingCart,
  Truck,
  Stethoscope,
  Home,
  Store,
  ShoppingBag,
  Briefcase,
  Link,
  Download,
} from "lucide-react";

/* =========================================================================
   STEP DEFINITIONS — vary by business type

   Kept deliberately short: one decision per step. Fiscal year, periods, and
   number/date formatting are defaulted (calendar year, 12 monthly periods,
   1,234,567.89, YYYY-MM-DD) and live behind "Advanced options" on the first
   step — all of them remain editable later in Settings → Company / Periods.
   ========================================================================= */

const STEPS_SHOPIFY = [
  { key: "start", icon: Store, label: "Your Business" },
  { key: "shopify", icon: Link, label: "Connect Store" },
  { key: "import", icon: Download, label: "Import Orders" },
  { key: "ready", icon: Rocket, label: "Ready" },
];

const STEPS_GENERAL = [
  { key: "start", icon: Store, label: "Your Business" },
  { key: "accounts", icon: BookOpen, label: "Chart of Accounts" },
  { key: "modules", icon: Puzzle, label: "Modules" },
  { key: "ready", icon: Rocket, label: "Ready" },
];

// General-path modules pre-checked on the Modules step (the common trio;
// clinic/properties are niche verticals the merchant opts into).
const RECOMMENDED_GENERAL_MODULES = ["sales", "purchases", "inventory"];

const MODULE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  inventory: Warehouse,
  sales: ShoppingCart,
  purchases: Truck,
  clinic: Stethoscope,
  properties: Home,
};

const MODULE_DESCRIPTIONS: Record<string, string> = {
  sales: "Create sales invoices and manage customer billing.",
  purchases: "Track purchase bills and vendor payments.",
  inventory: "Manage stock balances, adjustments, and ledger.",
  clinic: "Run a clinic with patients, doctors, visits, and billing.",
  properties: "Manage properties, leases, tenants, and rent collection.",
};

const DATE_FORMATS = [
  "YYYY-MM-DD",
  "DD/MM/YYYY",
  "MM/DD/YYYY",
  "DD-MM-YYYY",
  "DD.MM.YYYY",
];

// The wizard no longer asks for fiscal year / current period — both are
// derived from today. The submitted `fiscalYear` LABEL must match backend
// _fiscal_start_date (accounts/commands.py): years starting Jul–Dec are
// labeled by their END year, Jan–Jun by their start year. Anything shown
// to the user should use `startCalendarYear` (the calendar year P1 begins
// in), never the label. The wizard only ever offers January starts; a
// non-January startMonth can still arrive prefilled on the company row.
function deriveFiscal(startMonth: number): {
  fiscalYear: number;
  startCalendarYear: number;
  currentPeriod: number;
} {
  const now = new Date();
  const month = now.getMonth() + 1;
  const startCalendarYear =
    month >= startMonth ? now.getFullYear() : now.getFullYear() - 1;
  return {
    fiscalYear: startMonth <= 6 ? startCalendarYear : startCalendarYear + 1,
    startCalendarYear,
    currentPeriod: month - startMonth + (month >= startMonth ? 1 : 13),
  };
}

export default function OnboardingSetupPage() {
  const { t } = useTranslation("common");
  const router = useRouter();
  const { toast } = useToast();
  const { data: moduleList, isLoading: modulesLoading } = useModules();

  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState<OnboardingStatus | null>(null);

  // Business type
  const [businessType, setBusinessType] = useState<"shopify" | "general">("shopify");

  // Step: Your Business — company name + defaults behind "Advanced options"
  const [companyName, setCompanyName] = useState("");
  const [companyNameAr, setCompanyNameAr] = useState("");
  const [fiscalStartMonth, setFiscalStartMonth] = useState(1);
  const [thousandSep, setThousandSep] = useState(",");
  const [decimalSep, setDecimalSep] = useState(".");
  const [decimalPlaces, setDecimalPlaces] = useState(2);
  const [dateFormat, setDateFormat] = useState("YYYY-MM-DD");
  // A138: English-only (false) vs Arabic/bilingual (true). Drives whether the
  // optional Arabic data-entry fields are shown across the app. English-first default.
  const [enableArabicFields, setEnableArabicFields] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Step: CoA template (general path only)
  const [coaTemplate, setCoaTemplate] = useState("minimal");

  // Step: Modules (general path only) — pre-checked with the recommended
  // trio once the module list arrives, unless a draft restored a selection.
  const [selectedModules, setSelectedModules] = useState<Set<string>>(new Set());
  const [modulesInitialized, setModulesInitialized] = useState(false);

  // Step: Shopify connection
  const [shopifyDomain, setShopifyDomain] = useState("");
  const [shopifyConnecting, setShopifyConnecting] = useState(false);
  const [shopifyConnected, setShopifyConnected] = useState(false);
  const [shopifyStoreName, setShopifyStoreName] = useState("");

  // Step: Historical order import
  const [importMode, setImportMode] = useState<"all" | "from_date" | "skip">("all");
  const [importFromDate, setImportFromDate] = useState<string>(""); // YYYY-MM-DD

  const steps = businessType === "shopify" ? STEPS_SHOPIFY : STEPS_GENERAL;
  const lastStepIndex = steps.length - 1;
  const submitStepIndex = lastStepIndex - 1; // Step before "Ready"

  useEffect(() => {
    // A7: when returning from Shopify OAuth, the synchronous setStep below
    // races against the async draft restore inside getStatus().then() — the
    // async path wins and the user lands on the draft's earlier step ("Your
    // Business") instead of Connect Store. We skip restoring `step` from the
    // draft on OAuth return so the synchronous assignment lower in this
    // effect wins.
    const isShopifyReturn = router.query.shopify_connected === "true";

    onboardingService
      .getStatus()
      .then((data) => {
        setStatus(data);
        if (data.company.name) setCompanyName(data.company.name);
        if (data.company.name_ar) setCompanyNameAr(data.company.name_ar);
        if (data.company.fiscal_year_start_month)
          setFiscalStartMonth(data.company.fiscal_year_start_month);
        if (data.company.thousand_separator)
          setThousandSep(data.company.thousand_separator);
        if (data.company.decimal_separator)
          setDecimalSep(data.company.decimal_separator);
        if (data.company.decimal_places !== undefined)
          setDecimalPlaces(data.company.decimal_places);
        if (data.company.date_format)
          setDateFormat(data.company.date_format);
        if (data.company.enable_arabic_fields !== undefined)
          setEnableArabicFields(!!data.company.enable_arabic_fields);
        if (data.business_type === "shopify" || data.business_type === "general")
          setBusinessType(data.business_type);
        // Restore local draft. Key is versioned: v1 drafts stored indices
        // for the old 6-step flow, which would land on the wrong step now.
        try {
          sessionStorage.removeItem("onboarding_draft");
          const raw = sessionStorage.getItem("onboarding_draft_v2");
          if (raw) {
            const draft = JSON.parse(raw);
            // A7: don't override the OAuth-return step assignment.
            if (!isShopifyReturn && draft.step != null) setStep(draft.step);
            if (draft.coaTemplate) setCoaTemplate(draft.coaTemplate);
            if (Array.isArray(draft.selectedModules)) {
              setSelectedModules(new Set<string>(draft.selectedModules));
              setModulesInitialized(true);
            }
            if (draft.showAdvanced) setShowAdvanced(true);
            if (draft.shopifyConnected) setShopifyConnected(true);
            if (draft.shopifyStoreName) setShopifyStoreName(draft.shopifyStoreName);
            if (draft.importMode) setImportMode(draft.importMode);
            if (draft.importFromDate) setImportFromDate(draft.importFromDate);
          }
        } catch { /* sessionStorage unavailable */ }
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });

    // Check if Shopify store is already connected (e.g. returning from OAuth or page reload).
    // B4 contract: `connected` is true iff an ACTIVE row exists in `stores`.
    shopifyService.getStore().then(({ data }) => {
      const d = data as any;
      if (!d || d.connected !== true) return;
      const liveStores = (d.stores ?? []) as Array<{ shop_domain?: string; status?: string }>;
      const active = liveStores.find((s) => s.status === "ACTIVE");
      if (active) {
        setShopifyConnected(true);
        setShopifyStoreName(String(active.shop_domain || ""));
      }
    }).catch(() => { /* no store yet */ });

    // If returning from Shopify OAuth, jump to the shopify step
    if (isShopifyReturn) {
      setBusinessType("shopify");
      const shopifyStepIdx = STEPS_SHOPIFY.findIndex((s) => s.key === "shopify");
      if (shopifyStepIdx >= 0) setStep(shopifyStepIdx);
      setShopifyConnected(true);
      toast({ title: "Shopify store connected successfully!" });
      router.replace("/onboarding/setup", undefined, { shallow: true });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Pre-check the recommended modules once the list arrives — but only after
  // the status load settled (so a draft-restored selection isn't clobbered).
  useEffect(() => {
    if (loading || modulesInitialized || !moduleList) return;
    const optionalKeys = new Set(
      moduleList.filter((m) => !m.is_core).map((m) => m.key),
    );
    setSelectedModules(
      new Set(RECOMMENDED_GENERAL_MODULES.filter((k) => optionalKeys.has(k))),
    );
    setModulesInitialized(true);
  }, [loading, modulesInitialized, moduleList]);

  const toggleModule = (key: string) => {
    setSelectedModules((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const optionalModules = moduleList?.filter((m) => !m.is_core) ?? [];

      const effectiveImportMode: "all" | "from_date" | "skip" =
        businessType === "shopify" && shopifyConnected ? importMode : "skip";

      // Fiscal year & current period are derived, never asked (12 monthly
      // periods; a 13th adjustment period is Settings → Periods territory).
      const fiscal = deriveFiscal(fiscalStartMonth);

      const payload: OnboardingSetupPayload = {
        business_type: businessType,
        company_name: companyName,
        company_name_ar: companyNameAr,
        fiscal_year_start_month: fiscalStartMonth,
        thousand_separator: thousandSep,
        decimal_separator: decimalSep,
        decimal_places: decimalPlaces,
        date_format: dateFormat,
        enable_arabic_fields: enableArabicFields,
        fiscal_year: fiscal.fiscalYear,
        num_periods: 12,
        current_period: fiscal.currentPeriod,
        coa_template: businessType === "shopify" ? "retail" : coaTemplate,
        modules:
          businessType === "shopify"
            ? optionalModules.map((m) => ({
                key: m.key,
                is_enabled: ["shopify_connector", "sales", "purchases", "inventory"].includes(m.key),
              }))
            : optionalModules.map((m) => ({
                key: m.key,
                is_enabled: selectedModules.has(m.key),
              })),
        import_mode: effectiveImportMode,
        import_from_date:
          effectiveImportMode === "from_date" ? importFromDate : undefined,
      };

      await onboardingService.complete(payload);
      setStep(lastStepIndex);
    } catch (error) {
      toast({
        title: t("messages.error", "Error"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  // Save current progress to backend (fire-and-forget)
  const saveDraft = () => {
    // Save company fields to backend
    const draft: Record<string, unknown> = {};
    if (businessType) draft.business_type = businessType;
    if (companyName) draft.company_name = companyName;
    if (companyNameAr) draft.company_name_ar = companyNameAr;
    if (fiscalStartMonth) draft.fiscal_year_start_month = fiscalStartMonth;
    if (thousandSep) draft.thousand_separator = thousandSep;
    if (decimalSep) draft.decimal_separator = decimalSep;
    if (decimalPlaces >= 0) draft.decimal_places = decimalPlaces;
    if (dateFormat) draft.date_format = dateFormat;
    draft.enable_arabic_fields = enableArabicFields;
    if (Object.keys(draft).length > 0) {
      onboardingService.saveDraft(draft).catch(() => {});
    }
    // Save step + wizard-local fields (not on Company model)
    try {
      sessionStorage.setItem("onboarding_draft_v2", JSON.stringify({
        step,
        coaTemplate,
        // Only persist the selection once the precheck (or a prior restore)
        // has initialized it — an empty pre-init snapshot is otherwise
        // indistinguishable from a deliberate deselect-everything and would
        // suppress the recommended-modules precheck forever.
        ...(modulesInitialized
          ? { selectedModules: Array.from(selectedModules) }
          : {}),
        showAdvanced,
        shopifyConnected,
        shopifyStoreName,
        importMode,
        importFromDate,
      }));
    } catch { /* sessionStorage unavailable */ }
  };

  const handleNext = () => {
    if (step === submitStepIndex) {
      handleSubmit();
    } else if (step < lastStepIndex) {
      saveDraft();
      setStep(step + 1);
    }
  };

  const handleBack = () => {
    if (step > 0) setStep(step - 1);
  };

  const handleGoToDashboard = () => {
    // A28: always /dashboard now (was: routed Shopify users to
    // /shopify/reconciliation, which felt like a dead-end since the
    // celebration screen lists reconciliation as a recommended next
    // step). The dashboard is the merchant's home; reconciliation has
    // its own button below for Shopify users.
    window.location.href = "/dashboard";
  };

  const handleGoToReconciliation = () => {
    // F4/A166: land on the canonical reconciliation workspace. The
    // Shopify payout Verify flow still lives at /shopify/reconciliation,
    // linked from the workspace when needed.
    window.location.href = "/finance/reconciliation";
  };

  const handleSkip = () => {
    // Save progress before leaving — don't mark onboarding as completed
    saveDraft();
    router.push("/dashboard")
      .catch(() => {
        window.location.href = "/dashboard";
      });
  };

  if (loading) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <LoadingSpinner size="lg" />
        </div>
      </AppLayout>
    );
  }

  if (status?.onboarding_completed) {
    router.push("/dashboard");
    return null;
  }

  const MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  const currentStepKey = steps[step]?.key;
  const derivedFiscal = deriveFiscal(fiscalStartMonth);

  return (
    <AppLayout>
      <div className="mx-auto max-w-3xl py-6 px-4">
        {/* Progress indicator */}
        <div className="flex items-center justify-center gap-1 mb-8">
          {steps.map((s, i) => {
            const Icon = s.icon;
            const isActive = i === step;
            const isDone = i < step;
            return (
              <div key={s.key} className="flex items-center">
                <button
                  aria-label={s.label}
                  aria-current={isActive ? "step" : undefined}
                  onClick={() => i < step && setStep(i)}
                  disabled={i > step}
                  className={cn(
                    "flex items-center gap-2 rounded-full px-3 py-1.5 text-sm transition-all",
                    isActive && "bg-accent text-accent-foreground font-medium",
                    isDone && "text-accent cursor-pointer hover:bg-accent/10",
                    !isActive && !isDone && "text-muted-foreground"
                  )}
                >
                  {isDone ? (
                    <Check className="h-4 w-4" />
                  ) : (
                    <Icon className="h-4 w-4" />
                  )}
                  <span className="hidden sm:inline">{s.label}</span>
                </button>
                {i < steps.length - 1 && (
                  <div
                    className={cn(
                      "h-px w-6 mx-1",
                      i < step ? "bg-accent" : "bg-border"
                    )}
                  />
                )}
              </div>
            );
          })}
        </div>

        {/* Step content */}
        <Card>
          <CardContent className="p-6">
            {currentStepKey === "start" && (
              <StepStart
                businessType={businessType}
                onSelectBusinessType={(v) => {
                  setBusinessType(v);
                  if (v === "shopify") setCoaTemplate("retail");
                }}
                companyName={companyName}
                setCompanyName={setCompanyName}
                companyNameAr={companyNameAr}
                setCompanyNameAr={setCompanyNameAr}
                enableArabicFields={enableArabicFields}
                setEnableArabicFields={setEnableArabicFields}
                fiscalStartMonth={fiscalStartMonth}
                thousandSep={thousandSep}
                setThousandSep={setThousandSep}
                decimalSep={decimalSep}
                setDecimalSep={setDecimalSep}
                decimalPlaces={decimalPlaces}
                setDecimalPlaces={setDecimalPlaces}
                dateFormat={dateFormat}
                setDateFormat={setDateFormat}
                showAdvanced={showAdvanced}
                setShowAdvanced={setShowAdvanced}
                months={MONTHS}
                dateFormats={DATE_FORMATS}
              />
            )}
            {currentStepKey === "accounts" && (
              <StepChartOfAccounts
                templates={status?.templates ?? []}
                selected={coaTemplate}
                onSelect={setCoaTemplate}
              />
            )}
            {currentStepKey === "modules" && (
              <StepModules
                modules={moduleList?.filter((m) => !m.is_core) ?? []}
                selected={selectedModules}
                onToggle={toggleModule}
                isLoading={modulesLoading}
              />
            )}
            {currentStepKey === "import" && (
              <StepHistoricalImport
                mode={importMode}
                setMode={setImportMode}
                fromDate={importFromDate}
                setFromDate={setImportFromDate}
                fiscalYear={derivedFiscal.startCalendarYear}
                fiscalStartMonth={fiscalStartMonth}
                shopifyConnected={shopifyConnected}
              />
            )}
            {currentStepKey === "shopify" && (
              <StepShopifySetup
                domain={shopifyDomain}
                setDomain={setShopifyDomain}
                connecting={shopifyConnecting}
                connected={shopifyConnected}
                storeName={shopifyStoreName}
                onConnect={async () => {
                  if (!shopifyDomain.trim()) {
                    toast({ title: "Please enter your Shopify store domain.", variant: "destructive" });
                    return;
                  }
                  setShopifyConnecting(true);
                  try {
                    const { data } = await shopifyService.install(
                      shopifyDomain.trim(),
                      isShopifyEmbedded(),
                    );
                    // A7: persist the current step (Connect Store) before
                    // leaving for OAuth. Without this, the draft still has
                    // the previous step (saved by handleNext when user
                    // advanced INTO this step) and the post-OAuth restore
                    // sends us back there.
                    saveDraft();
                    // Save return point so we come back to onboarding after OAuth
                    sessionStorage.setItem("shopify_return_to", "onboarding");
                    // B13: use the embedded-aware redirect so the iframe
                    // case (rare during onboarding but possible) escapes
                    // to top level for Shopify's OAuth page.
                    redirectTopLevel(data.url);
                  } catch {
                    toast({ title: "Failed to connect. Check your store domain.", variant: "destructive" });
                    setShopifyConnecting(false);
                  }
                }}
              />
            )}
            {currentStepKey === "ready" && (
              <StepReady
                businessType={businessType}
                shopifyConnected={shopifyConnected}
                onGoToDashboard={handleGoToDashboard}
                onGoToReconciliation={handleGoToReconciliation}
                fiscalYear={derivedFiscal.startCalendarYear}
                fiscalStartMonth={fiscalStartMonth}
                thousandSep={thousandSep}
                decimalSep={decimalSep}
                decimalPlaces={decimalPlaces}
                dateFormat={dateFormat}
                enableArabicFields={enableArabicFields}
                months={MONTHS}
              />
            )}
          </CardContent>
        </Card>

        {/* Navigation buttons */}
        {currentStepKey !== "ready" && (
          <div className="flex items-center justify-between mt-6">
            <div className="flex items-center gap-2">
              {step > 0 && (
                <Button variant="ghost" onClick={handleBack}>
                  <ArrowLeft className="h-4 w-4 me-2" />
                  {t("actions.back", "Back")}
                </Button>
              )}
              <Button variant="ghost" onClick={handleSkip}>
                {t("onboarding.completeLater", "Complete later")}
              </Button>
            </div>
            <Button onClick={handleNext} disabled={submitting}>
              {submitting ? (
                <LoadingSpinner size="sm" className="me-2" />
              ) : null}
              {step === submitStepIndex
                ? t("onboarding.finishSetup", "Finish Setup")
                : t("actions.next", "Next")}
              {!submitting && <ArrowRight className="h-4 w-4 ms-2" />}
            </Button>
          </div>
        )}
      </div>
    </AppLayout>
  );
}

/* =========================================================================
   STEP COMPONENTS
   ========================================================================= */

function StepStart({
  businessType,
  onSelectBusinessType,
  companyName,
  setCompanyName,
  companyNameAr,
  setCompanyNameAr,
  enableArabicFields,
  setEnableArabicFields,
  fiscalStartMonth,
  thousandSep,
  setThousandSep,
  decimalSep,
  setDecimalSep,
  decimalPlaces,
  setDecimalPlaces,
  dateFormat,
  setDateFormat,
  showAdvanced,
  setShowAdvanced,
  months,
  dateFormats,
}: {
  businessType: "shopify" | "general";
  onSelectBusinessType: (v: "shopify" | "general") => void;
  companyName: string;
  setCompanyName: (v: string) => void;
  companyNameAr: string;
  setCompanyNameAr: (v: string) => void;
  enableArabicFields: boolean;
  setEnableArabicFields: (v: boolean) => void;
  fiscalStartMonth: number;
  thousandSep: string;
  setThousandSep: (v: string) => void;
  decimalSep: string;
  setDecimalSep: (v: string) => void;
  decimalPlaces: number;
  setDecimalPlaces: (v: number) => void;
  dateFormat: string;
  setDateFormat: (v: string) => void;
  showAdvanced: boolean;
  setShowAdvanced: (v: boolean) => void;
  months: string[];
  dateFormats: string[];
}) {
  const options = [
    {
      key: "shopify" as const,
      icon: ShoppingBag,
      title: "Shopify Merchant",
      description:
        "I sell on Shopify and need to track orders, payouts, fees, and refunds in my accounting.",
      badge: "Recommended",
    },
    {
      key: "general" as const,
      icon: Briefcase,
      title: "General Accounting",
      description:
        "I need a general-purpose accounting system with journal entries, invoicing, and reports.",
      badge: null,
    },
  ];

  return (
    <div>
      <h2 className="text-xl font-semibold mb-1">How do you use Nxentra?</h2>
      <p className="text-sm text-muted-foreground mb-6">
        Choose your primary use case — everything else is set up with sensible
        defaults you can change anytime in Settings.
      </p>

      <div className="grid gap-4">
        {options.map((opt) => {
          const Icon = opt.icon;
          const isSelected = businessType === opt.key;
          return (
            <button
              key={opt.key}
              type="button"
              onClick={() => onSelectBusinessType(opt.key)}
              className={cn(
                "flex items-start gap-4 rounded-xl border-2 p-5 text-start transition-all",
                isSelected
                  ? "border-accent bg-accent/5"
                  : "border-border hover:border-muted-foreground/30"
              )}
            >
              <div
                className={cn(
                  "flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-lg",
                  isSelected
                    ? "bg-accent/10 text-accent"
                    : "bg-muted text-muted-foreground"
                )}
              >
                <Icon className="h-6 w-6" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-base">{opt.title}</span>
                  {opt.badge && (
                    <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-accent/10 text-accent">
                      {opt.badge}
                    </span>
                  )}
                </div>
                <p className="text-sm text-muted-foreground mt-1">
                  {opt.description}
                </p>
              </div>
              <div
                className={cn(
                  "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full border-2 mt-1 transition-colors",
                  isSelected
                    ? "border-accent bg-accent text-primary-foreground"
                    : "border-muted-foreground/30"
                )}
              >
                {isSelected && <Check className="h-3.5 w-3.5" />}
              </div>
            </button>
          );
        })}
      </div>

      <div className={cn("mt-6", enableArabicFields && "grid grid-cols-1 sm:grid-cols-2 gap-4")}>
        <div>
          <Label htmlFor="companyName">Company Name</Label>
          <Input
            id="companyName"
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
          />
        </div>
        {enableArabicFields && (
          <div>
            <Label htmlFor="companyNameAr">Company Name (Arabic)</Label>
            <Input
              id="companyNameAr"
              value={companyNameAr}
              onChange={(e) => setCompanyNameAr(e.target.value)}
              dir="rtl"
              placeholder="Optional"
            />
          </div>
        )}
      </div>

      {/* Everything below used to be two wizard steps (Company Profile
          formatting + Fiscal Year). All of it defaults sensibly and stays
          editable in Settings → Company, so it hides behind one disclosure. */}
      <div className="mt-6 border-t pt-4">
        <button
          type="button"
          onClick={() => setShowAdvanced(!showAdvanced)}
          aria-expanded={showAdvanced}
          className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronDown
            className={cn("h-4 w-4 transition-transform", showAdvanced && "rotate-180")}
          />
          Advanced options — fiscal year &amp; formatting
        </button>
        {!showAdvanced && (
          <p className="mt-1.5 text-xs text-muted-foreground">
            Fiscal year starts {months[fiscalStartMonth - 1]}, 12 monthly
            periods &middot; numbers like{" "}
            {formatPreview(thousandSep, decimalSep, decimalPlaces)} &middot;
            dates as {dateFormat} &middot;{" "}
            {enableArabicFields ? "bilingual data entry" : "English data entry"}
          </p>
        )}
        {showAdvanced && (
          <div className="mt-4 grid gap-4">
            {/* A138: data-entry language choice. English-only hides the optional
                Arabic fields across the app; bilingual shows them. */}
            <div>
              <Label htmlFor="dataLanguage">Data entry fields</Label>
              <select
                id="dataLanguage"
                value={enableArabicFields ? "bilingual" : "english"}
                onChange={(e) => setEnableArabicFields(e.target.value === "bilingual")}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="english">English only</option>
                <option value="bilingual">Bilingual (English &amp; Arabic)</option>
              </select>
              <p className="mt-1 text-xs text-muted-foreground">
                Bilingual adds optional Arabic name, description, and address
                fields on forms.
              </p>
            </div>

            {/* Fiscal start month is deliberately NOT selectable here: the
                backend's period seeding only supports the calendar-aligned
                default end-to-end (see NEXT_TASKS A236 — labeling-convention
                unification). Disclose it instead of offering it. */}
            <div className="rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">
              Your fiscal year starts {months[fiscalStartMonth - 1]} with 12
              monthly periods, created automatically. Manage periods later in
              Settings &rarr; Periods.
            </div>

            <div>
              <h3 className="text-sm font-medium mb-3">Number &amp; Date Formatting</h3>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div>
                  <Label htmlFor="thousandSep">Thousands</Label>
                  <select
                    id="thousandSep"
                    value={thousandSep}
                    onChange={(e) => setThousandSep(e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    {/* "None" (empty string) is not offered: every
                        persistence layer treats "" as not-provided and would
                        silently keep the comma while the summary claims
                        otherwise. */}
                    <option value=",">, (comma)</option>
                    <option value=".">. (dot)</option>
                    <option value=" ">(space)</option>
                  </select>
                </div>
                <div>
                  <Label htmlFor="decimalSep">Decimal</Label>
                  <select
                    id="decimalSep"
                    value={decimalSep}
                    onChange={(e) => setDecimalSep(e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value=".">. (dot)</option>
                    <option value=",">, (comma)</option>
                  </select>
                </div>
                <div>
                  <Label htmlFor="decPlaces">Decimal Places</Label>
                  <select
                    id="decPlaces"
                    value={decimalPlaces}
                    onChange={(e) => setDecimalPlaces(Number(e.target.value))}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value={0}>0</option>
                    <option value={2}>2</option>
                    <option value={3}>3</option>
                  </select>
                </div>
                <div>
                  <Label htmlFor="dateFormat">Date Format</Label>
                  <select
                    id="dateFormat"
                    value={dateFormat}
                    onChange={(e) => setDateFormat(e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    {dateFormats.map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <p className="text-xs text-muted-foreground mt-2">
                Preview: {formatPreview(thousandSep, decimalSep, decimalPlaces)}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function StepShopifySetup({
  domain,
  setDomain,
  connecting,
  connected,
  storeName,
  onConnect,
}: {
  domain: string;
  setDomain: (v: string) => void;
  connecting: boolean;
  connected: boolean;
  storeName: string;
  onConnect: () => void;
}) {
  return (
    <div>
      <h2 className="text-xl font-semibold mb-1">Connect Your Shopify Store</h2>
      <p className="text-sm text-muted-foreground mb-6">
        Link your store to automatically track orders, payouts, refunds, and fees.
      </p>

      {connected ? (
        <div className="rounded-xl border-2 border-green-500/30 bg-green-500/5 p-8 text-center">
          <div className="flex justify-center mb-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-green-500/10">
              <Check className="h-7 w-7 text-green-500" />
            </div>
          </div>
          <h3 className="font-medium mb-2">Store Connected</h3>
          <p className="text-sm text-muted-foreground font-mono">{storeName}</p>
          <p className="text-sm text-muted-foreground mt-2">
            Your store is linked. Click &quot;Finish Setup&quot; to complete onboarding.
          </p>
        </div>
      ) : (
        <div className="rounded-xl border-2 border-dashed border-border p-8">
          <div className="flex justify-center mb-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-accent/10">
              <Store className="h-7 w-7 text-accent" />
            </div>
          </div>
          <div className="max-w-md mx-auto space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="shopify-domain">Store Domain</Label>
              <Input
                id="shopify-domain"
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="my-store.myshopify.com"
              />
              <p className="text-xs text-muted-foreground">
                Enter your store name or full .myshopify.com domain
              </p>
            </div>
            <Button onClick={onConnect} disabled={connecting} className="w-full">
              {connecting ? (
                <>
                  <ArrowRight className="me-2 h-4 w-4 animate-spin" />
                  Connecting...
                </>
              ) : (
                <>
                  <Link className="me-2 h-4 w-4" />
                  Connect to Shopify
                </>
              )}
            </Button>
            <p className="text-xs text-muted-foreground text-center">
              You&apos;ll be redirected to Shopify to authorize the connection,
              then brought back here automatically.
            </p>
          </div>
        </div>
      )}

      <div className="mt-6 rounded-lg bg-muted/50 p-4">
        <h4 className="text-sm font-medium mb-2">What&apos;s been configured for you:</h4>
        <ul className="text-sm text-muted-foreground space-y-1.5">
          <li className="flex items-center gap-2">
            <Check className="h-3.5 w-3.5 text-green-500 flex-shrink-0" />
            Retail chart of accounts (revenue, COGS, tax, fees)
          </li>
          <li className="flex items-center gap-2">
            <Check className="h-3.5 w-3.5 text-green-500 flex-shrink-0" />
            Shopify clearing account for payout tracking
          </li>
          <li className="flex items-center gap-2">
            <Check className="h-3.5 w-3.5 text-green-500 flex-shrink-0" />
            Payment processing fee account
          </li>
          <li className="flex items-center gap-2">
            <Check className="h-3.5 w-3.5 text-green-500 flex-shrink-0" />
            Automatic journal entries for orders, refunds, and payouts
          </li>
        </ul>
      </div>

      {!connected && (
        <p className="text-xs text-muted-foreground mt-4 text-center">
          You can also skip this and connect later from Settings &rarr; Integrations.
        </p>
      )}
    </div>
  );
}

function StepHistoricalImport({
  mode,
  setMode,
  fromDate,
  setFromDate,
  fiscalYear,
  fiscalStartMonth,
  shopifyConnected,
}: {
  mode: "all" | "from_date" | "skip";
  setMode: (v: "all" | "from_date" | "skip") => void;
  fromDate: string;
  setFromDate: (v: string) => void;
  fiscalYear: number;
  fiscalStartMonth: number;
  shopifyConnected: boolean;
}) {
  const fiscalYearStartISO = `${fiscalYear}-${String(fiscalStartMonth).padStart(2, "0")}-01`;

  const handleSelect = (v: "all" | "from_date" | "skip") => {
    setMode(v);
    if (v === "from_date" && !fromDate) {
      setFromDate(fiscalYearStartISO);
    }
  };

  const options: {
    key: "all" | "from_date" | "skip";
    title: string;
    description: string;
    badge?: string;
  }[] = [
    {
      key: "all",
      title: "Import all historical orders",
      description:
        "Pull every paid order your store has ever had. Best if you want a complete accounting history in Nxentra.",
      badge: "Recommended",
    },
    {
      key: "from_date",
      title: "Import from a specific date",
      description:
        "Only pull orders placed on or after a date you choose. Good for a clean cutoff — e.g. the start of your fiscal year.",
    },
    {
      key: "skip",
      title: "Start fresh — only sync new orders from today",
      description:
        "Don't backfill anything. Use this if you already keep books elsewhere and don't want to double-count historical orders.",
    },
  ];

  return (
    <div>
      <h2 className="text-xl font-semibold mb-1">Import Historical Orders</h2>
      <p className="text-sm text-muted-foreground mb-6">
        Choose how much of your Shopify order history you&apos;d like to bring
        into Nxentra. You can always re-run an import later from Settings.
      </p>

      {!shopifyConnected && (
        <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-600 dark:text-amber-400">
          Shopify isn&apos;t connected yet. You can finish onboarding and set up
          the import later from Settings &rarr; Integrations.
        </div>
      )}

      <div className="grid gap-3">
        {options.map((opt) => {
          const isSelected = mode === opt.key;
          return (
            <button
              key={opt.key}
              type="button"
              onClick={() => handleSelect(opt.key)}
              disabled={!shopifyConnected}
              className={cn(
                "flex items-start gap-4 rounded-xl border-2 p-4 text-start transition-all",
                isSelected
                  ? "border-accent bg-accent/5"
                  : "border-border hover:border-muted-foreground/30",
                !shopifyConnected && "opacity-50 cursor-not-allowed"
              )}
            >
              <div
                className={cn(
                  "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full border-2 mt-0.5 transition-colors",
                  isSelected
                    ? "border-accent bg-accent text-primary-foreground"
                    : "border-muted-foreground/30"
                )}
              >
                {isSelected && <Check className="h-3.5 w-3.5" />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{opt.title}</span>
                  {opt.badge && (
                    <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-accent/10 text-accent">
                      {opt.badge}
                    </span>
                  )}
                </div>
                <p className="text-sm text-muted-foreground mt-1">
                  {opt.description}
                </p>
                {opt.key === "from_date" && isSelected && (
                  <div className="mt-3" onClick={(e) => e.stopPropagation()}>
                    <Label htmlFor="import-from-date" className="text-xs">
                      Start date
                    </Label>
                    <Input
                      id="import-from-date"
                      type="date"
                      value={fromDate}
                      onChange={(e) => setFromDate(e.target.value)}
                      className="mt-1 max-w-xs"
                    />
                    <p className="text-xs text-muted-foreground mt-1">
                      Defaulted to the start of your fiscal year.
                    </p>
                  </div>
                )}
              </div>
            </button>
          );
        })}
      </div>

      <div className="mt-6 rounded-lg bg-muted/50 p-4 text-xs text-muted-foreground">
        <p>
          <strong className="text-foreground">How this works:</strong> paid
          orders (Paymob, PayPal, etc.) are booked to your ledger on import.
          Pending orders — including cash-on-delivery — are captured for
          visibility and will post to accounting automatically once Shopify
          marks them paid.
        </p>
      </div>
    </div>
  );
}

function formatPreview(
  thousandSep: string,
  decimalSep: string,
  decimalPlaces: number
): string {
  const whole = "1" + thousandSep + "234" + thousandSep + "567";
  if (decimalPlaces === 0) return whole;
  const dec = decimalSep + "8".repeat(decimalPlaces);
  return whole + dec;
}

function StepChartOfAccounts({
  templates,
  selected,
  onSelect,
}: {
  templates: CoaTemplate[];
  selected: string;
  onSelect: (key: string) => void;
}) {
  return (
    <div>
      <h2 className="text-xl font-semibold mb-1">Chart of Accounts</h2>
      <p className="text-sm text-muted-foreground mb-6">
        Choose a starting template. You can always add, edit, or delete accounts
        later.
      </p>

      <div className="grid gap-3">
        {templates.map((tmpl) => {
          const isSelected = tmpl.key === selected;
          return (
            <button
              key={tmpl.key}
              type="button"
              onClick={() => onSelect(tmpl.key)}
              className={cn(
                "flex items-start gap-4 rounded-xl border-2 p-4 text-start transition-all",
                isSelected
                  ? "border-accent bg-accent/5"
                  : "border-border hover:border-muted-foreground/30"
              )}
            >
              <div
                className={cn(
                  "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full border-2 mt-0.5 transition-colors",
                  isSelected
                    ? "border-accent bg-accent text-primary-foreground"
                    : "border-muted-foreground/30"
                )}
              >
                {isSelected && <Check className="h-3.5 w-3.5" />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-medium">{tmpl.label}</div>
                <p className="text-sm text-muted-foreground mt-0.5">
                  {tmpl.description}
                </p>
                {tmpl.account_count > 0 && (
                  <p className="text-xs text-muted-foreground mt-1">
                    +{tmpl.account_count} accounts (on top of system accounts)
                  </p>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function StepModules({
  modules,
  selected,
  onToggle,
  isLoading,
}: {
  modules: ModuleInfo[];
  selected: Set<string>;
  onToggle: (key: string) => void;
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-xl font-semibold mb-1">Modules</h2>
      <p className="text-sm text-muted-foreground mb-6">
        Enable the modules your business needs. Core accounting is always
        included. You can change this anytime in Settings.
      </p>

      <div className="grid gap-3">
        {modules.map((mod) => {
          const isSelected = selected.has(mod.key);
          const Icon = MODULE_ICONS[mod.key] ?? BookOpen;
          return (
            <button
              key={mod.key}
              type="button"
              onClick={() => onToggle(mod.key)}
              className={cn(
                "flex items-center gap-4 rounded-xl border-2 p-4 text-start transition-all",
                isSelected
                  ? "border-accent bg-accent/5"
                  : "border-border hover:border-muted-foreground/30"
              )}
            >
              <div
                className={cn(
                  "flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-lg",
                  isSelected
                    ? "bg-accent/10 text-accent"
                    : "bg-muted text-muted-foreground"
                )}
              >
                <Icon className="h-6 w-6" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-medium">{mod.label}</div>
                <p className="text-sm text-muted-foreground">
                  {MODULE_DESCRIPTIONS[mod.key] || ""}
                </p>
              </div>
              <div
                className={cn(
                  "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full border-2 transition-colors",
                  isSelected
                    ? "border-accent bg-accent text-primary-foreground"
                    : "border-muted-foreground/30"
                )}
              >
                {isSelected && <Check className="h-3.5 w-3.5" />}
              </div>
            </button>
          );
        })}
      </div>

      {modules.length === 0 && (
        <p className="text-center text-muted-foreground py-8">
          No optional modules available.
        </p>
      )}
    </div>
  );
}

function StepReady({
  businessType,
  shopifyConnected,
  onGoToDashboard,
  onGoToReconciliation,
  fiscalYear,
  fiscalStartMonth,
  thousandSep,
  decimalSep,
  decimalPlaces,
  dateFormat,
  enableArabicFields,
  months,
}: {
  businessType: string;
  shopifyConnected: boolean;
  onGoToDashboard: () => void;
  onGoToReconciliation: () => void;
  fiscalYear: number;
  fiscalStartMonth: number;
  thousandSep: string;
  decimalSep: string;
  decimalPlaces: number;
  dateFormat: string;
  enableArabicFields: boolean;
  months: string[];
}) {
  const isShopify = businessType === "shopify";
  const router = useRouter();

  const shopifySteps = [
    {
      done: shopifyConnected,
      label: "Connect your Shopify store",
      action: shopifyConnected ? undefined : () => router.push("/shopify/settings"),
    },
    {
      done: false,
      label: "Review your chart of accounts",
      action: () => router.push("/accounting/chart-of-accounts"),
    },
    {
      done: false,
      label: "Check your first reconciliation",
      action: () => router.push("/finance/reconciliation"),
    },
  ];

  const generalSteps = [
    {
      done: false,
      label: "Review your chart of accounts",
      action: () => router.push("/accounting/chart-of-accounts"),
    },
    {
      done: false,
      label: "Create your first customer",
      action: () => router.push("/accounting/customers/new"),
    },
    {
      done: false,
      label: "Create your first journal entry",
      action: () => router.push("/accounting/journal-entries/new"),
    },
    {
      done: false,
      label: "View the trial balance",
      action: () => router.push("/reports/trial-balance"),
    },
  ];

  const nextSteps = isShopify ? shopifySteps : generalSteps;

  return (
    <div className="py-8">
      <div className="text-center">
        <div className="flex justify-center mb-4">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-green-500/10">
            <Check className="h-8 w-8 text-green-500" />
          </div>
        </div>
        <h2 className="text-xl font-semibold mb-2">You&apos;re All Set!</h2>
        <p className="text-muted-foreground mb-6 max-w-md mx-auto">
          {isShopify
            ? "Your Shopify accounting is configured. Here\u2019s what to do next:"
            : "Your company is configured and ready. Here\u2019s what to do next:"}
        </p>
      </div>

      {/* Honest-disclosure line: the wizard chose these defaults without
          asking, so say what they are and where to change them. */}
      <div className="max-w-md mx-auto mb-8 rounded-lg bg-muted/50 p-4 text-xs text-muted-foreground text-center">
        <span className="font-medium text-foreground">Set up for you: </span>
        fiscal year starting {months[fiscalStartMonth - 1]} {fiscalYear} with
        12 monthly periods &middot; numbers like{" "}
        {formatPreview(thousandSep, decimalSep, decimalPlaces)} &middot; dates
        as {dateFormat} &middot;{" "}
        {enableArabicFields ? "bilingual data entry" : "English data entry"}
        {" \u2014 "}
        <button
          type="button"
          onClick={() => router.push("/settings/company")}
          className="underline hover:text-foreground"
        >
          change in Settings
        </button>
      </div>

      <div className="max-w-md mx-auto mb-8">
        <h3 className="text-sm font-medium text-muted-foreground mb-3 uppercase tracking-wide">
          Recommended next steps
        </h3>
        <div className="space-y-2">
          {nextSteps.map((item, i) => (
            <button
              key={i}
              type="button"
              onClick={item.action}
              disabled={!item.action}
              className={cn(
                "flex items-center gap-3 w-full rounded-lg border p-3 text-start text-sm transition-colors",
                item.done
                  ? "border-green-500/30 bg-green-500/5"
                  : "border-border hover:border-accent/50 hover:bg-accent/5"
              )}
            >
              <div
                className={cn(
                  "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full border-2",
                  item.done
                    ? "border-green-500 bg-green-500 text-white"
                    : "border-muted-foreground/30"
                )}
              >
                {item.done ? (
                  <Check className="h-3.5 w-3.5" />
                ) : (
                  <span className="text-xs text-muted-foreground">{i + 1}</span>
                )}
              </div>
              <span className={item.done ? "text-muted-foreground line-through" : ""}>
                {item.label}
              </span>
              {item.action && !item.done && (
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground ms-auto" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* A28: For Shopify merchants, present BOTH "Go to Dashboard"
          (primary — the merchant's home) and "Go to Reconciliation"
          (secondary — the workflow recommended in the next-steps
          list above). Pre-A28 the wizard offered only Reconciliation,
          which felt like a dead-end since merchants expected the
          dashboard. Non-Shopify merchants still see one button. */}
      <div className="flex flex-col items-center gap-3">
        {isShopify ? (
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
            <Button size="lg" onClick={onGoToDashboard} className="min-w-[200px]">
              Go to Dashboard
              <ArrowRight className="h-4 w-4 ms-2" />
            </Button>
            <Button
              size="lg"
              variant="outline"
              onClick={onGoToReconciliation}
              className="min-w-[200px]"
            >
              Go to Reconciliation
              <ArrowRight className="h-4 w-4 ms-2" />
            </Button>
          </div>
        ) : (
          <Button size="lg" onClick={onGoToDashboard} className="min-w-[200px]">
            Go to Dashboard
            <ArrowRight className="h-4 w-4 ms-2" />
          </Button>
        )}
        <p className="text-xs text-muted-foreground">
          You can always access these from the sidebar.
        </p>
      </div>
    </div>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common"])),
    },
  };
};
