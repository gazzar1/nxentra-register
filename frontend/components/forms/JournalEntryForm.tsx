import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "next-i18next";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ArabicField } from "@/components/forms/ArabicField";
import { useArabicFields } from "@/hooks/useArabicFields";
import { CompanyDateInput } from "@/components/ui/CompanyDateInput";
import { FormattedAmountInput } from "@/components/ui/FormattedAmountInput";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAccounts, useDimensions } from "@/queries/useAccounts";
import { accountsService } from "@/services/accounts.service";
import { exchangeRatesService } from "@/services/exchange-rates.service";
import { useAuth } from "@/contexts/AuthContext";
import { useBilingualText } from "@/components/common/BilingualText";
import type {
  JournalEntryCreatePayload,
  AnalysisTagInput,
  PilotAdjustmentSourceKind,
} from "@/types/journal";
import type { AccountAnalysisDefault, AnalysisDimension } from "@/types/account";
import { periodsService, FiscalPeriod } from "@/services/periods.service";
import { currencyOptions } from "@/lib/constants";
import { formatAmount as fmtAmount } from "@/lib/currency";
import { cn } from "@/lib/cn";
import { useFormKeyboardShortcuts } from "@/lib/useFormKeyboardShortcuts";
import { isConstrainedPilot } from "@/lib/constrained-pilot";
import {
  PILOT_ADJUSTMENT_SOURCE_KINDS,
  pilotAdjustmentKindLabel,
  pilotAdjustmentReferenceHint,
} from "@/lib/pilot-adjustments";

const analysisTagSchema = z.object({
  dimension_id: z.number(),
  dimension_value_id: z.number(),
});

const journalLineSchema = z.object({
  account_id: z.number().positive("Account is required"),
  description: z.string().optional(),
  description_ar: z.string().optional(),
  debit: z.number().min(0),
  credit: z.number().min(0),
  analysis_tags: z.array(analysisTagSchema).optional(),
}).refine(
  (data) => !(data.debit > 0 && data.credit > 0),
  { message: "Cannot have both debit and credit on same line" }
);

const journalEntrySchema = z
  .object({
    date: z.string().min(1, "Date is required"),
    period: z.number().optional(),
    memo: z.string().optional(),
    memo_ar: z.string().optional(),
    currency: z.string().optional(),
    exchange_rate: z.number().optional(),
    // A5-PR4b: pilot-adjustment source. Both-or-neither (below); the fields are
    // only rendered/sent under the active constrained pilot — see handleSubmit.
    adjustment_source_kind: z.string().optional(),
    adjustment_source_reference: z.string().optional(),
    lines: z.array(journalLineSchema).min(2, "At least 2 lines required"),
  })
  .refine(
    (d) =>
      Boolean((d.adjustment_source_kind || "").trim()) ===
      Boolean((d.adjustment_source_reference || "").trim()),
    {
      message: "Provide both a source type and a reference, or leave both blank.",
      path: ["adjustment_source_reference"],
    }
  );

type JournalEntryFormData = z.infer<typeof journalEntrySchema>;

// What the form hands back for the pilot-adjustment source. '' means "no source /
// cleared"; a known kind means "set to this source". The caller (create/edit page)
// decides whether to send it, so it can suppress an unchanged or system-owned source.
export interface PilotAdjustmentSourceSelection {
  kind: PilotAdjustmentSourceKind | "";
  reference: string;
}

interface JournalEntryFormProps {
  initialData?: Partial<JournalEntryFormData>;
  onSubmit: (
    data: JournalEntryCreatePayload,
    saveAsDraft: boolean,
    source?: PilotAdjustmentSourceSelection
  ) => Promise<void>;
  isSubmitting?: boolean;
  onCancel?: () => void;
  // A5-PR4b: when editing a draft whose CURRENT stamp is system-owned (a nonblank
  // source_module other than pilot_adjustment), the manual form shows it read-only
  // and never emits adjustment fields — trying to edit/clear a system stamp is
  // refused server-side.
  systemOwnedSource?: { module: string; document: string } | null;
}

export function JournalEntryForm({
  initialData,
  onSubmit,
  isSubmitting,
  onCancel,
  systemOwnedSource = null,
}: JournalEntryFormProps) {
  const { t } = useTranslation(["common", "accounting"]);
  const { company } = useAuth();
  // A5-PR4b: under the active constrained pilot, every manual post is a supervised
  // pilot adjustment. The editable evidence section shows only when the entry is
  // not already system-owned.
  const pilotActive = isConstrainedPilot(company?.pilot_profile);
  const showAdjustmentSource = pilotActive && !systemOwnedSource;
  const getText = useBilingualText();
  const { data: accounts } = useAccounts({ status: "ACTIVE" });
  const { data: dimensions } = useDimensions();
  const [openPeriods, setOpenPeriods] = useState<FiscalPeriod[]>([]);
  const [exchangeRateLoading, setExchangeRateLoading] = useState(false);
  const [isForeignCurrency, setIsForeignCurrency] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const functionalCurrency = company?.functional_currency || company?.default_currency || "USD";

  // Cache account analysis defaults: accountCode -> defaults[]
  const [accountDefaultsCache, setAccountDefaultsCache] = useState<
    Record<string, AccountAnalysisDefault[]>
  >({});
  // Track available dimensions per line index (based on account selection)
  const [lineDimensions, setLineDimensions] = useState<
    Record<number, AnalysisDimension[]>
  >({});

  const form = useForm<JournalEntryFormData>({
    resolver: zodResolver(journalEntrySchema),
    defaultValues: {
      date: initialData?.date || new Date().toISOString().split("T")[0],
      period: initialData?.period,
      memo: initialData?.memo || "",
      memo_ar: initialData?.memo_ar || "",
      currency: initialData?.currency || company?.default_currency || "USD",
      exchange_rate: initialData?.exchange_rate,
      adjustment_source_kind: initialData?.adjustment_source_kind || "",
      adjustment_source_reference: initialData?.adjustment_source_reference || "",
      lines: initialData?.lines || [
        { account_id: 0, debit: 0, credit: 0, description: "", description_ar: "", analysis_tags: [] },
        { account_id: 0, debit: 0, credit: 0, description: "", description_ar: "", analysis_tags: [] },
      ],
    },
  });

  // Reset form when initialData is first available (important for nested arrays like analysis_tags)
  const hasResetRef = useRef(false);
  useEffect(() => {
    if (initialData && !hasResetRef.current) {
      hasResetRef.current = true;
      form.reset({
        date: initialData.date || new Date().toISOString().split("T")[0],
        period: initialData.period,
        memo: initialData.memo || "",
        memo_ar: initialData.memo_ar || "",
        currency: initialData.currency || company?.default_currency || "USD",
        exchange_rate: initialData.exchange_rate,
        // A5-PR4b: hydrate source fields via reset (not the Select's onValueChange),
        // so a valid prefilled/initial reference is preserved during hydration.
        adjustment_source_kind: initialData.adjustment_source_kind || "",
        adjustment_source_reference: initialData.adjustment_source_reference || "",
        lines: initialData.lines || [
          { account_id: 0, debit: 0, credit: 0, description: "", description_ar: "", analysis_tags: [] },
          { account_id: 0, debit: 0, credit: 0, description: "", description_ar: "", analysis_tags: [] },
        ],
      });
    }
  }, [initialData, form]);

  // Fetch analysis defaults for an account and update line dimensions
  const handleAccountChange = useCallback(
    async (lineIndex: number, accountId: number) => {
      form.setValue(`lines.${lineIndex}.account_id`, accountId);
      // Clear previous analysis tags for this line
      form.setValue(`lines.${lineIndex}.analysis_tags`, []);

      if (!accountId || !dimensions) {
        setLineDimensions((prev) => ({ ...prev, [lineIndex]: [] }));
        return;
      }

      const account = accounts?.find((a) => a.id === accountId);
      if (!account) {
        setLineDimensions((prev) => ({ ...prev, [lineIndex]: [] }));
        return;
      }

      // Check cache first
      let defaults = accountDefaultsCache[account.code];
      if (!defaults) {
        try {
          const { data } = await accountsService.getAnalysisDefaults(account.code);
          defaults = data;
          setAccountDefaultsCache((prev) => ({ ...prev, [account.code]: data }));
        } catch {
          defaults = [];
        }
      }

      // Find "Journal Entry" type dimensions that have defaults on this account
      // Journal Entry type = applies_to_account_types is empty []
      const journalEntryDimensions = dimensions.filter((dim) => {
        // Check if this dimension is "Journal Entry" type (empty applies_to_account_types)
        if (dim.applies_to_account_types.length > 0) return false;
        // Check if this dimension has a default set on this account
        return defaults.some((d) => d.dimension === dim.id);
      });

      setLineDimensions((prev) => ({ ...prev, [lineIndex]: journalEntryDimensions }));
    },
    [accounts, dimensions, accountDefaultsCache, form]
  );

  // Auto-fetch exchange rate when currency or date changes
  const watchedCurrency = form.watch("currency");
  const watchedDate = form.watch("date");

  useEffect(() => {
    const currency = watchedCurrency || company?.default_currency || "USD";
    const isForeign = currency !== functionalCurrency;
    setIsForeignCurrency(isForeign);

    if (!isForeign) {
      form.setValue("exchange_rate", undefined);
      return;
    }

    if (!watchedDate) return;

    setExchangeRateLoading(true);
    exchangeRatesService
      .lookup({ from_currency: currency, to_currency: functionalCurrency, date: watchedDate })
      .then(({ data }) => {
        if (data.rate) {
          // Round to 6 decimal places to match backend field precision
          form.setValue("exchange_rate", Math.round(parseFloat(data.rate) * 1000000) / 1000000);
        }
      })
      .catch(() => {
        // No rate found — user can enter manually
      })
      .finally(() => setExchangeRateLoading(false));
  }, [watchedCurrency, watchedDate, functionalCurrency]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    periodsService.list().then(({ data }) => {
      const open = (data.periods || []).filter((p) => p.status === "OPEN");
      setOpenPeriods(open);
      // Default to the current period if not already set
      const current = open.find((p) => p.is_current);
      if (current && !form.getValues("period")) {
        form.setValue("period", current.period);
      }
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Initialize line dimensions for existing lines (when editing)
  useEffect(() => {
    if (!initialData?.lines || !accounts || !dimensions) return;
    const lines = initialData.lines;

    const initLineDimensions = async () => {
      const newLineDimensions: Record<number, AnalysisDimension[]> = {};
      const newCache: Record<string, AccountAnalysisDefault[]> = { ...accountDefaultsCache };

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (!line.account_id) continue;

        const account = accounts.find((a) => a.id === line.account_id);
        if (!account) continue;

        // Fetch defaults if not cached
        let defaults = newCache[account.code];
        if (!defaults) {
          try {
            const { data } = await accountsService.getAnalysisDefaults(account.code);
            defaults = data;
            newCache[account.code] = data;
          } catch {
            defaults = [];
          }
        }

        // Find Journal Entry type dimensions with defaults on this account
        const journalEntryDimensions = dimensions.filter((dim) => {
          if (dim.applies_to_account_types.length > 0) return false;
          return defaults.some((d) => d.dimension === dim.id);
        });

        if (journalEntryDimensions.length > 0) {
          newLineDimensions[i] = journalEntryDimensions;
        }
      }

      setAccountDefaultsCache(newCache);
      setLineDimensions(newLineDimensions);
    };

    initLineDimensions();
  }, [initialData?.lines, accounts, dimensions]); // eslint-disable-line react-hooks/exhaustive-deps

  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: "lines",
  });

  const watchedLines = form.watch("lines");

  // Calculate totals
  const totals = watchedLines.reduce(
    (acc, line) => ({
      debit: acc.debit + (line.debit || 0),
      credit: acc.credit + (line.credit || 0),
    }),
    { debit: 0, credit: 0 }
  );

  const difference = Math.abs(totals.debit - totals.credit);
  const isBalanced = difference < 0.01;

  const selectedCurrency = form.watch("currency") || company?.default_currency || "USD";
  const watchedExchangeRate = form.watch("exchange_rate");
  const companyFmt = company ? {
    thousand_separator: company.thousand_separator,
    decimal_separator: company.decimal_separator,
    decimal_places: company.decimal_places,
  } : undefined;
  const formatCurrency = (amount: number, currency?: string) => {
    if (companyFmt) {
      return `${currency || selectedCurrency} ${fmtAmount(amount, companyFmt)}`;
    }
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || selectedCurrency,
      minimumFractionDigits: 2,
    }).format(amount);
  };

  // Converted totals for foreign currency entries
  const convertedTotals = isForeignCurrency && watchedExchangeRate
    ? {
        debit: totals.debit * watchedExchangeRate,
        credit: totals.credit * watchedExchangeRate,
      }
    : null;

  const handleSubmit = async (data: JournalEntryFormData, saveAsDraft: boolean) => {
    // Filter out empty lines
    const validLines = data.lines.filter(
      (line) => line.account_id > 0 && (line.debit > 0 || line.credit > 0)
    );

    // A5-PR4b: the pilot-adjustment source is passed separately (not in the
    // payload) so the page can decide whether to send it. Both-or-neither is
    // already enforced by the schema refine before we get here.
    let source: PilotAdjustmentSourceSelection | undefined;
    if (showAdjustmentSource) {
      source = {
        kind: (data.adjustment_source_kind || "").trim() as PilotAdjustmentSourceKind | "",
        reference: (data.adjustment_source_reference || "").trim(),
      };
    }

    await onSubmit(
      {
        date: data.date,
        period: data.period,
        memo: data.memo,
        memo_ar: data.memo_ar,
        currency: data.currency,
        exchange_rate: data.exchange_rate,
        lines: validLines.map((line, index) => ({
          line_no: index + 1,
          account_id: line.account_id,
          description: line.description,
          description_ar: line.description_ar,
          debit: line.debit,
          credit: line.credit,
          analysis_tags: (line.analysis_tags || []).filter(
            (tag) => tag.dimension_id && tag.dimension_value_id
          ),
        })),
      },
      saveAsDraft,
      source
    );
  };

  // Postable accounts only
  const postableAccounts = accounts?.filter((a) => a.is_postable && !a.is_header) || [];

  useFormKeyboardShortcuts({
    formRef,
    onSave: () => form.handleSubmit((data) => handleSubmit(data, false))(),
    onSubmit: () => form.handleSubmit((data) => handleSubmit(data, true))(),
    onCancel,
    enabled: !isSubmitting,
  });

  // A138: hide the optional Arabic memo field unless the company enabled Arabic fields.
  const showArabic = useArabicFields();

  return (
    <form ref={formRef} className="space-y-6">
      {/* Header Fields */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="date">{t("accounting:journalEntry.date")} *</Label>
          <CompanyDateInput
            id="date"
            value={form.watch("date")}
            onChange={(iso) => form.setValue("date", iso, { shouldValidate: true })}
            dateFormat={(company?.date_format as "DD/MM/YYYY" | "MM/DD/YYYY" | "DD-MM-YYYY" | "DD.MM.YYYY" | "YYYY-MM-DD") || "YYYY-MM-DD"}
            showPeriodWarning
          />
          {form.formState.errors.date && (
            <p className="text-sm text-destructive">
              {form.formState.errors.date.message}
            </p>
          )}
        </div>
        <div className="space-y-2">
          <Label>{t("accounting:journalEntry.period", "Period")}</Label>
          <Select
            value={form.watch("period")?.toString() || ""}
            onValueChange={(value) => form.setValue("period", parseInt(value))}
          >
            <SelectTrigger>
              <SelectValue placeholder={t("accounting:journalEntry.period", "Period")} />
            </SelectTrigger>
            <SelectContent>
              {openPeriods.map((p) => (
                <SelectItem key={p.period} value={p.period.toString()}>
                  {String(p.period).padStart(3, "0")}/{p.fiscal_year}
                  {p.is_current && ` (${t("accounting:journalEntry.currentPeriod", "Current")})`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label>Currency</Label>
          <Select
            value={form.watch("currency") || company?.default_currency || "USD"}
            onValueChange={(value) => form.setValue("currency", value)}
          >
            <SelectTrigger>
              <SelectValue placeholder="Currency" />
            </SelectTrigger>
            <SelectContent>
              {currencyOptions.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {isForeignCurrency && (
          <div className="space-y-2">
            <Label htmlFor="exchange_rate">
              Exchange Rate (1 {watchedCurrency} = ? {functionalCurrency})
            </Label>
            <Input
              id="exchange_rate"
              type="number"
              step="0.00000001"
              min="0"
              value={form.watch("exchange_rate") ?? ""}
              onChange={(e) => {
                const val = e.target.value;
                form.setValue("exchange_rate", val ? parseFloat(val) : undefined);
              }}
              placeholder={exchangeRateLoading ? "Looking up..." : "e.g. 3.75"}
              disabled={exchangeRateLoading}
            />
            {!exchangeRateLoading && form.watch("exchange_rate") && (
              <p className="text-xs text-muted-foreground">
                Amounts will be converted to {functionalCurrency} at this rate when posted
              </p>
            )}
          </div>
        )}
      </div>

      {/* A5-PR4b: pilot adjustment evidence (active pilot, non system-owned) */}
      {showAdjustmentSource && (
        <div className="space-y-4 rounded-lg border border-primary/30 bg-primary/5 p-4">
          <div>
            <h3 className="text-sm font-semibold text-primary">Pilot adjustment evidence</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Required before posting. Saving an incomplete or draft entry does not
              resolve the source item.
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label>Source type</Label>
              <Select
                value={form.watch("adjustment_source_kind") || ""}
                onValueChange={(value) => {
                  const prev = form.getValues("adjustment_source_kind");
                  form.setValue("adjustment_source_kind", value, { shouldValidate: true });
                  // Clear an incompatible manually-entered reference on a real change
                  // (hydration goes through form.reset, not this handler).
                  if (value !== prev) {
                    form.setValue("adjustment_source_reference", "", { shouldValidate: true });
                  }
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a source type" />
                </SelectTrigger>
                <SelectContent>
                  {PILOT_ADJUSTMENT_SOURCE_KINDS.map((k) => (
                    <SelectItem key={k} value={k}>
                      {pilotAdjustmentKindLabel(k)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="adjustment_source_reference">Source reference</Label>
              <Input
                id="adjustment_source_reference"
                {...form.register("adjustment_source_reference")}
                placeholder="Source reference"
              />
              {form.watch("adjustment_source_kind") && (
                <p className="text-xs text-muted-foreground">
                  {pilotAdjustmentReferenceHint(form.watch("adjustment_source_kind") || "")}
                </p>
              )}
              {form.formState.errors.adjustment_source_reference && (
                <p className="text-sm text-destructive">
                  {form.formState.errors.adjustment_source_reference.message}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* A5-PR4b: system-owned provenance is read-only under the active pilot */}
      {pilotActive && systemOwnedSource && (
        <div className="space-y-2 rounded-lg border bg-muted/40 p-4">
          <h3 className="text-sm font-semibold">System provenance</h3>
          <p className="text-xs text-muted-foreground">
            This entry belongs to an automated process. Its source provenance is
            system-owned and cannot be relabelled through the manual form.
          </p>
          <dl className="grid gap-1 text-sm">
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Module:</dt>
              <dd className="font-mono break-all">{systemOwnedSource.module}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Document:</dt>
              <dd className="font-mono break-all">{systemOwnedSource.document || "—"}</dd>
            </div>
          </dl>
        </div>
      )}

      <div className={showArabic ? "grid gap-4 sm:grid-cols-2" : "grid gap-4"}>
        <div className="space-y-2">
          <Label htmlFor="memo">
            {pilotActive ? "Adjustment reason" : t("accounting:journalEntry.memo")}
          </Label>
          <Input
            id="memo"
            {...form.register("memo")}
            placeholder={pilotActive ? "Why this adjustment is needed..." : "Description..."}
          />
          {pilotActive && (
            <p className="text-xs text-muted-foreground">
              Required before posting · 10–180 characters
            </p>
          )}
        </div>
        <ArabicField>
          <div className="space-y-2">
            <Label htmlFor="memo_ar">{t("accounting:journalEntry.memoAr")}</Label>
            <Input
              id="memo_ar"
              {...form.register("memo_ar")}
              placeholder="البيان..."
              dir="rtl"
            />
          </div>
        </ArabicField>
      </div>

      {/* Lines */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <Label>{t("accounting:journalEntries.lines")}</Label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              append({ account_id: 0, debit: 0, credit: 0, description: "", description_ar: "", analysis_tags: [] })
            }
          >
            <Plus className="me-2 h-4 w-4" />
            {t("accounting:journalEntries.addLine")}
          </Button>
        </div>

        <div className="rounded-lg border">
          {/* Header */}
          <div className="grid grid-cols-12 gap-2 border-b bg-muted p-3 text-sm font-medium">
            <div className="col-span-1">#</div>
            <div className={isForeignCurrency ? "col-span-3" : "col-span-4"}>{t("accounting:journalLine.account")}</div>
            <div className={isForeignCurrency ? "col-span-2" : "col-span-3"}>{t("accounting:journalLine.description")}</div>
            <div className="col-span-2 text-end">
              {t("accounting:journalLine.debit")}
              {isForeignCurrency && <span className="text-xs text-muted-foreground ms-1">({selectedCurrency})</span>}
            </div>
            <div className="col-span-2 text-end">
              {t("accounting:journalLine.credit")}
              {isForeignCurrency && <span className="text-xs text-muted-foreground ms-1">({selectedCurrency})</span>}
            </div>
            {isForeignCurrency && (
              <div className="col-span-2 text-end text-blue-500">
                {functionalCurrency}
              </div>
            )}
          </div>

          {/* Lines */}
          {fields.map((field, index) => {
            const lineAnalysisDimensions = lineDimensions[index] || [];
            const currentTags = watchedLines[index]?.analysis_tags || [];

            return (
              <div key={field.id} className="border-b">
                <div className="grid grid-cols-12 gap-2 p-3 items-center">
                  <div className="col-span-1 text-sm text-muted-foreground">
                    {index + 1}
                  </div>
                  <div className={isForeignCurrency ? "col-span-3" : "col-span-4"}>
                    <Select
                      value={watchedLines[index]?.account_id?.toString() || ""}
                      onValueChange={(value) => handleAccountChange(index, parseInt(value))}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder={t("accounting:journalLine.account")} />
                      </SelectTrigger>
                      <SelectContent>
                        {postableAccounts.map((account) => (
                          <SelectItem key={account.id} value={account.id.toString()}>
                            <span className="font-mono text-xs me-2">{account.code}</span>
                            {getText(account.name, account.name_ar)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className={isForeignCurrency ? "col-span-2" : "col-span-3"}>
                    <Input
                      {...form.register(`lines.${index}.description`)}
                      placeholder={t("accounting:journalLine.description")}
                    />
                  </div>
                  <div className="col-span-2">
                    <FormattedAmountInput
                      value={watchedLines[index]?.debit || 0}
                      onChange={(v) => form.setValue(`lines.${index}.debit`, v)}
                      settings={companyFmt}
                      className="text-end ltr-number"
                      disabled={watchedLines[index]?.credit > 0}
                    />
                  </div>
                  <div className="col-span-2 flex items-center gap-2">
                    <FormattedAmountInput
                      value={watchedLines[index]?.credit || 0}
                      onChange={(v) => form.setValue(`lines.${index}.credit`, v)}
                      settings={companyFmt}
                      className="text-end ltr-number"
                      disabled={watchedLines[index]?.debit > 0}
                    />
                    {!isForeignCurrency && fields.length > 2 && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          remove(index);
                          setLineDimensions((prev) => {
                            const updated = { ...prev };
                            delete updated[index];
                            return updated;
                          });
                        }}
                        className="shrink-0"
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    )}
                  </div>
                  {isForeignCurrency && (
                    <div className="col-span-2 flex items-center gap-2">
                      <span className="text-end ltr-number text-sm text-blue-500 w-full text-end">
                        {watchedExchangeRate
                          ? formatCurrency(
                              ((watchedLines[index]?.debit || 0) + (watchedLines[index]?.credit || 0)) * watchedExchangeRate,
                              functionalCurrency
                            )
                          : "-"}
                      </span>
                      {fields.length > 2 && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => {
                            remove(index);
                            setLineDimensions((prev) => {
                              const updated = { ...prev };
                              delete updated[index];
                              return updated;
                            });
                          }}
                          className="shrink-0"
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      )}
                    </div>
                  )}
                </div>

                {/* Analysis Dimensions for this line */}
                {lineAnalysisDimensions.length > 0 && (
                  <div className="px-3 pb-3 pt-0">
                    <div className="flex flex-wrap gap-3 ps-8 text-sm">
                      {lineAnalysisDimensions.map((dim) => {
                        const currentTag = currentTags.find((t) => t.dimension_id === dim.id);
                        return (
                          <div key={dim.id} className="flex items-center gap-2">
                            <span className="text-muted-foreground text-xs">
                              {getText(dim.name, dim.name_ar)}:
                            </span>
                            <Select
                              value={currentTag?.dimension_value_id?.toString() || ""}
                              onValueChange={(value) => {
                                const newTags = currentTags.filter((t) => t.dimension_id !== dim.id);
                                if (value) {
                                  newTags.push({
                                    dimension_id: dim.id,
                                    dimension_value_id: parseInt(value),
                                  });
                                }
                                form.setValue(`lines.${index}.analysis_tags`, newTags);
                              }}
                            >
                              <SelectTrigger className="h-8 w-40 text-xs">
                                <SelectValue placeholder={t("actions.select", "Select...")} />
                              </SelectTrigger>
                              <SelectContent>
                                {dim.values?.map((val) => (
                                  <SelectItem key={val.id} value={val.id.toString()}>
                                    <span className="font-mono text-xs me-1">{val.code}</span>
                                    {getText(val.name, val.name_ar)}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {/* Totals */}
          <div className="grid grid-cols-12 gap-2 bg-muted p-3 font-medium">
            <div className="col-span-1"></div>
            <div className={isForeignCurrency ? "col-span-3" : "col-span-4"}>{t("accounting:totals.totalDebit")}</div>
            <div className={isForeignCurrency ? "col-span-2" : "col-span-3"}></div>
            <div className="col-span-2 text-end ltr-number">
              {formatCurrency(totals.debit)}
            </div>
            <div className="col-span-2 text-end ltr-number">
              {formatCurrency(totals.credit)}
            </div>
            {isForeignCurrency && (
              <div className="col-span-2 text-end ltr-number text-blue-500">
                {convertedTotals
                  ? formatCurrency(convertedTotals.debit, functionalCurrency)
                  : "-"}
              </div>
            )}
          </div>
        </div>

        {/* Balance indicator */}
        <div className="flex items-center justify-end gap-4">
          <span className="text-sm text-muted-foreground">
            {t("accounting:totals.difference")}: {formatCurrency(difference)}
          </span>
          <span
            className={cn(
              "text-sm font-medium",
              isBalanced ? "text-green-400" : "text-red-400"
            )}
          >
            {isBalanced ? t("status.balanced") : t("status.unbalanced")}
          </span>
        </div>

        {form.formState.errors.lines && (
          <p className="text-sm text-destructive">
            {form.formState.errors.lines.message}
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-4">
        <Button
          type="button"
          onClick={form.handleSubmit((data) => handleSubmit(data, false))}
          disabled={isSubmitting}
          variant="outline"
        >
          {isSubmitting ? t("actions.loading") : t("actions.save")} (Incomplete)
        </Button>
        <Button
          type="button"
          onClick={form.handleSubmit((data) => handleSubmit(data, true))}
          disabled={isSubmitting || !isBalanced}
        >
          {isSubmitting ? t("actions.loading") : t("actions.save")} (Draft)
        </Button>
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            {t("actions.cancel")}
          </Button>
        )}
      </div>
    </form>
  );
}
