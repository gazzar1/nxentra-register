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
import {
  Building2,
  Calendar,
  BookOpen,
  Puzzle,
  Rocket,
  Check,
  ArrowRight,
  ArrowLeft,
  Warehouse,
  ShoppingCart,
  Truck,
  Stethoscope,
  Home,
  Store,
  ShoppingBag,
  Briefcase,
  Link,
} from "lucide-react";

/* =========================================================================
   STEP DEFINITIONS — vary by business type
   ========================================================================= */

const STEPS_SHOPIFY = [
  { key: "business", icon: Store, label: "Business Type" },
  { key: "company", icon: Building2, label: "Company Profile" },
  { key: "fiscal", icon: Calendar, label: "Fiscal Year" },
  { key: "shopify", icon: Link, label: "Shopify Setup" },
  { key: "ready", icon: Rocket, label: "Ready" },
];

const STEPS_GENERAL = [
  { key: "business", icon: Store, label: "Business Type" },
  { key: "company", icon: Building2, label: "Company Profile" },
  { key: "fiscal", icon: Calendar, label: "Fiscal Year" },
  { key: "accounts", icon: BookOpen, label: "Chart of Accounts" },
  { key: "modules", icon: Puzzle, label: "Modules" },
  { key: "ready", icon: Rocket, label: "Ready" },
];

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

  // Step: Company profile
  const [companyName, setCompanyName] = useState("");
  const [companyNameAr, setCompanyNameAr] = useState("");
  const [fiscalStartMonth, setFiscalStartMonth] = useState(1);
  const [thousandSep, setThousandSep] = useState(",");
  const [decimalSep, setDecimalSep] = useState(".");
  const [decimalPlaces, setDecimalPlaces] = useState(2);
  const [dateFormat, setDateFormat] = useState("YYYY-MM-DD");

  // Step: Fiscal year
  const [fiscalYear, setFiscalYear] = useState(new Date().getFullYear());
  const [numPeriods, setNumPeriods] = useState(12);
  const [currentPeriod, setCurrentPeriod] = useState(new Date().getMonth() + 1);

  // Step: CoA template (general path only)
  const [coaTemplate, setCoaTemplate] = useState("minimal");

  // Step: Modules (general path only)
  const [selectedModules, setSelectedModules] = useState<Set<string>>(new Set());

  // Step: Shopify connection
  const [shopifyDomain, setShopifyDomain] = useState("");
  const [shopifyConnecting, setShopifyConnecting] = useState(false);
  const [shopifyConnected, setShopifyConnected] = useState(false);
  const [shopifyStoreName, setShopifyStoreName] = useState("");

  const steps = businessType === "shopify" ? STEPS_SHOPIFY : STEPS_GENERAL;
  const lastStepIndex = steps.length - 1;
  const submitStepIndex = lastStepIndex - 1; // Step before "Ready"

  useEffect(() => {
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
        if (data.business_type === "shopify" || data.business_type === "general")
          setBusinessType(data.business_type);
        // Restore local draft (step, fiscal fields)
        try {
          const raw = sessionStorage.getItem("onboarding_draft");
          if (raw) {
            const draft = JSON.parse(raw);
            if (draft.step != null) setStep(draft.step);
            if (draft.fiscalYear) setFiscalYear(draft.fiscalYear);
            if (draft.numPeriods) setNumPeriods(draft.numPeriods);
            if (draft.currentPeriod) setCurrentPeriod(draft.currentPeriod);
            if (draft.coaTemplate) setCoaTemplate(draft.coaTemplate);
            if (draft.shopifyConnected) setShopifyConnected(true);
            if (draft.shopifyStoreName) setShopifyStoreName(draft.shopifyStoreName);
          }
        } catch { /* sessionStorage unavailable */ }
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });

    // Check if Shopify store is already connected (e.g. returning from OAuth or page reload)
    shopifyService.getStore().then(({ data }) => {
      // API returns { connected: false } when no store, or full store object when connected
      if (!data || ("connected" in data && !data.connected)) return;
      if ("shop_domain" in data && "status" in data) {
        if (data.status === "ACTIVE") {
          setShopifyConnected(true);
          setShopifyStoreName(String(data.shop_domain || ""));
        }
      }
    }).catch(() => { /* no store yet */ });

    // If returning from Shopify OAuth, jump to the shopify step
    if (router.query.shopify_connected === "true") {
      setBusinessType("shopify");
      const shopifyStepIdx = STEPS_SHOPIFY.findIndex((s) => s.key === "shopify");
      if (shopifyStepIdx >= 0) setStep(shopifyStepIdx);
      setShopifyConnected(true);
      toast({ title: "Shopify store connected successfully!" });
      router.replace("/onboarding/setup", undefined, { shallow: true });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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

      const payload: OnboardingSetupPayload = {
        business_type: businessType,
        company_name: companyName,
        company_name_ar: companyNameAr,
        fiscal_year_start_month: fiscalStartMonth,
        thousand_separator: thousandSep,
        decimal_separator: decimalSep,
        decimal_places: decimalPlaces,
        date_format: dateFormat,
        fiscal_year: fiscalYear,
        num_periods: numPeriods,
        current_period: currentPeriod,
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
    if (Object.keys(draft).length > 0) {
      onboardingService.saveDraft(draft).catch(() => {});
    }
    // Save step + fiscal fields locally (not on Company model)
    try {
      sessionStorage.setItem("onboarding_draft", JSON.stringify({
        step,
        fiscalYear,
        numPeriods,
        currentPeriod,
        coaTemplate,
        shopifyConnected,
        shopifyStoreName,
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
    const dest = businessType === "shopify" ? "/shopify/reconciliation" : "/dashboard";
    window.location.href = dest;
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
            {currentStepKey === "business" && (
              <StepBusinessType
                selected={businessType}
                onSelect={(v) => {
                  setBusinessType(v);
                  if (v === "shopify") setCoaTemplate("retail");
                }}
              />
            )}
            {currentStepKey === "company" && (
              <StepCompanyProfile
                companyName={companyName}
                setCompanyName={setCompanyName}
                companyNameAr={companyNameAr}
                setCompanyNameAr={setCompanyNameAr}
                fiscalStartMonth={fiscalStartMonth}
                setFiscalStartMonth={setFiscalStartMonth}
                thousandSep={thousandSep}
                setThousandSep={setThousandSep}
                decimalSep={decimalSep}
                setDecimalSep={setDecimalSep}
                decimalPlaces={decimalPlaces}
                setDecimalPlaces={setDecimalPlaces}
                dateFormat={dateFormat}
                setDateFormat={setDateFormat}
                months={MONTHS}
                dateFormats={DATE_FORMATS}
              />
            )}
            {currentStepKey === "fiscal" && (
              <StepFiscalYear
                fiscalYear={fiscalYear}
                setFiscalYear={setFiscalYear}
                numPeriods={numPeriods}
                setNumPeriods={setNumPeriods}
                currentPeriod={currentPeriod}
                setCurrentPeriod={setCurrentPeriod}
                fiscalStartMonth={fiscalStartMonth}
                months={MONTHS}
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
                    const { data } = await shopifyService.install(shopifyDomain.trim());
                    // Save return point so we come back to onboarding after OAuth
                    sessionStorage.setItem("shopify_return_to", "onboarding");
                    window.location.href = data.url;
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

function StepBusinessType({
  selected,
  onSelect,
}: {
  selected: "shopify" | "general";
  onSelect: (v: "shopify" | "general") => void;
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
        Choose your primary use case. This configures your accounts, modules, and
        dashboard for the best experience. You can always change this later.
      </p>

      <div className="grid gap-4">
        {options.map((opt) => {
          const Icon = opt.icon;
          const isSelected = selected === opt.key;
          return (
            <button
              key={opt.key}
              type="button"
              onClick={() => onSelect(opt.key)}
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

function StepCompanyProfile({
  companyName,
  setCompanyName,
  companyNameAr,
  setCompanyNameAr,
  fiscalStartMonth,
  setFiscalStartMonth,
  thousandSep,
  setThousandSep,
  decimalSep,
  setDecimalSep,
  decimalPlaces,
  setDecimalPlaces,
  dateFormat,
  setDateFormat,
  months,
  dateFormats,
}: {
  companyName: string;
  setCompanyName: (v: string) => void;
  companyNameAr: string;
  setCompanyNameAr: (v: string) => void;
  fiscalStartMonth: number;
  setFiscalStartMonth: (v: number) => void;
  thousandSep: string;
  setThousandSep: (v: string) => void;
  decimalSep: string;
  setDecimalSep: (v: string) => void;
  decimalPlaces: number;
  setDecimalPlaces: (v: number) => void;
  dateFormat: string;
  setDateFormat: (v: string) => void;
  months: string[];
  dateFormats: string[];
}) {
  return (
    <div>
      <h2 className="text-xl font-semibold mb-1">Company Profile</h2>
      <p className="text-sm text-muted-foreground mb-6">
        Confirm your company details and formatting preferences.
      </p>

      <div className="grid gap-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <Label htmlFor="companyName">Company Name</Label>
            <Input
              id="companyName"
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
            />
          </div>
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
        </div>

        <div>
          <Label htmlFor="fiscalStart">Fiscal Year Start Month</Label>
          <select
            id="fiscalStart"
            value={fiscalStartMonth}
            onChange={(e) => setFiscalStartMonth(Number(e.target.value))}
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            {months.map((m, i) => (
              <option key={i} value={i + 1}>
                {m}
              </option>
            ))}
          </select>
        </div>

        <div className="border-t pt-4 mt-2">
          <h3 className="text-sm font-medium mb-3">Number & Date Formatting</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div>
              <Label htmlFor="thousandSep">Thousands</Label>
              <select
                id="thousandSep"
                value={thousandSep}
                onChange={(e) => setThousandSep(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value=",">, (comma)</option>
                <option value=".">. (dot)</option>
                <option value=" ">(space)</option>
                <option value="">None</option>
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

function StepFiscalYear({
  fiscalYear,
  setFiscalYear,
  numPeriods,
  setNumPeriods,
  currentPeriod,
  setCurrentPeriod,
  fiscalStartMonth,
  months,
}: {
  fiscalYear: number;
  setFiscalYear: (v: number) => void;
  numPeriods: number;
  setNumPeriods: (v: number) => void;
  currentPeriod: number;
  setCurrentPeriod: (v: number) => void;
  fiscalStartMonth: number;
  months: string[];
}) {
  const periodLabels = [];
  for (let i = 0; i < Math.min(numPeriods, 12); i++) {
    const monthIdx = (fiscalStartMonth - 1 + i) % 12;
    periodLabels.push(months[monthIdx]);
  }
  if (numPeriods === 13) {
    periodLabels.push("Adj");
  }

  return (
    <div>
      <h2 className="text-xl font-semibold mb-1">Fiscal Year & Periods</h2>
      <p className="text-sm text-muted-foreground mb-6">
        Set up your first fiscal year. Periods will be created automatically.
      </p>

      <div className="grid gap-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <Label htmlFor="fiscalYear">Fiscal Year</Label>
            <Input
              id="fiscalYear"
              type="number"
              min={2020}
              max={2030}
              value={fiscalYear}
              onChange={(e) => setFiscalYear(Number(e.target.value))}
            />
          </div>
          <div>
            <Label htmlFor="numPeriods">Number of Periods</Label>
            <select
              id="numPeriods"
              value={numPeriods}
              onChange={(e) => setNumPeriods(Number(e.target.value))}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              <option value={12}>12 (Monthly)</option>
              <option value={13}>13 (Monthly + Adjustment)</option>
            </select>
          </div>
          <div>
            <Label htmlFor="currentPeriod">Current Period</Label>
            <select
              id="currentPeriod"
              value={currentPeriod}
              onChange={(e) => setCurrentPeriod(Number(e.target.value))}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              {periodLabels.map((label, i) => (
                <option key={i} value={i + 1}>
                  P{i + 1} - {label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Visual period timeline */}
        <div className="border rounded-lg p-4 mt-2">
          <h3 className="text-sm font-medium mb-3">Period Timeline</h3>
          <div className="flex flex-wrap gap-1.5">
            {periodLabels.map((label, i) => (
              <div
                key={i}
                className={cn(
                  "px-2.5 py-1 rounded text-xs font-medium border",
                  i + 1 === currentPeriod
                    ? "bg-accent text-accent-foreground border-accent"
                    : i + 1 < currentPeriod
                      ? "bg-muted text-muted-foreground border-transparent"
                      : "border-border text-foreground"
                )}
              >
                {label}
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            Starts {months[fiscalStartMonth - 1]} {fiscalYear}
          </p>
        </div>
      </div>
    </div>
  );
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
}: {
  businessType: string;
  shopifyConnected: boolean;
  onGoToDashboard: () => void;
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
      action: () => router.push("/shopify/reconciliation"),
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
        <p className="text-muted-foreground mb-8 max-w-md mx-auto">
          {isShopify
            ? "Your Shopify accounting is configured. Here\u2019s what to do next:"
            : "Your company is configured and ready. Here\u2019s what to do next:"}
        </p>
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

      <div className="flex flex-col items-center gap-3">
        <Button size="lg" onClick={onGoToDashboard} className="min-w-[200px]">
          {isShopify ? "Go to Reconciliation" : "Go to Dashboard"}
          <ArrowRight className="h-4 w-4 ms-2" />
        </Button>
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
