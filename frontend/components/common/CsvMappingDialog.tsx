import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";

export type ColumnMapping = {
  date_column: string;
  description_column: string;
  amount_column: string;
  reference_column: string;
  debit_column: string;
  credit_column: string;
  date_format: string;
};

const NONE = "__none__";

const DATE_FORMATS = [
  { value: "%Y-%m-%d", label: "YYYY-MM-DD (e.g. 2026-04-01)" },
  { value: "%d/%m/%Y", label: "DD/MM/YYYY (e.g. 01/04/2026)" },
  { value: "%m/%d/%Y", label: "MM/DD/YYYY (e.g. 04/01/2026)" },
  { value: "%d-%m-%Y", label: "DD-MM-YYYY (e.g. 01-04-2026)" },
  { value: "%m-%d-%Y", label: "MM-DD-YYYY (e.g. 04-01-2026)" },
  { value: "%d.%m.%Y", label: "DD.MM.YYYY (e.g. 01.04.2026)" },
  { value: "%m.%d.%Y", label: "MM.DD.YYYY (e.g. 04.01.2026)" },
  { value: "%Y/%m/%d", label: "YYYY/MM/DD (e.g. 2026/04/01)" },
];

/**
 * Detect the strptime date format from ALL sample date cells.
 *
 * For NN<sep>NN<sep>YYYY dates we cannot tell DD/MM from MM/DD by looking at a
 * single ambiguous row (e.g. "03/04/2026"). We scan every sample: a component
 * greater than 12 can only be the day, which pins the order. If NO sample
 * disambiguates, the file is genuinely ambiguous and the caller must make the
 * user choose — silently assuming DD/MM is exactly what transposed day/month on
 * US-format imports (A128). Returns `{ format: "", ambiguous: true }` in that
 * case.
 */
export function detectDateFormat(samples: string[]): { format: string; ambiguous: boolean } {
  const dates = samples.map((s) => (s ?? "").trim()).filter(Boolean);
  if (dates.length === 0) return { format: "%Y-%m-%d", ambiguous: false };

  // Year-first shapes are unambiguous.
  if (dates.every((d) => /^\d{4}-\d{2}-\d{2}/.test(d))) return { format: "%Y-%m-%d", ambiguous: false };
  if (dates.every((d) => /^\d{4}\/\d{2}\/\d{2}/.test(d))) return { format: "%Y/%m/%d", ambiguous: false };

  // NN<sep>NN<sep>YYYY with sep ∈ { / - . }.
  const shape = dates.find((d) => /^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}/.test(d));
  if (shape) {
    const sep = shape.match(/^\d{1,2}([/.\-])/)![1];
    let dayFirstEvidence = false; // first field > 12 → it must be the day
    let monthFirstEvidence = false; // second field > 12 → the day is second → month first
    for (const d of dates) {
      const m = d.match(/^(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{4}/);
      if (!m) continue;
      const a = parseInt(m[1], 10);
      const b = parseInt(m[2], 10);
      if (a > 12 && a <= 31) dayFirstEvidence = true;
      if (b > 12 && b <= 31) monthFirstEvidence = true;
    }
    const dayFirst = sep === "/" ? "%d/%m/%Y" : sep === "-" ? "%d-%m-%Y" : "%d.%m.%Y";
    const monthFirst = sep === "/" ? "%m/%d/%Y" : sep === "-" ? "%m-%d-%Y" : "%m.%d.%Y";
    if (dayFirstEvidence && !monthFirstEvidence) return { format: dayFirst, ambiguous: false };
    if (monthFirstEvidence && !dayFirstEvidence) return { format: monthFirst, ambiguous: false };
    // Both kinds of evidence (garbled) or neither (every row ≤ 12) → can't tell.
    return { format: "", ambiguous: true };
  }

  // Unrecognized shape (textual months, etc.) — don't block; the user can pick.
  return { format: "%Y-%m-%d", ambiguous: false };
}

function matchHeader(headers: string[], hints: string[]): string {
  const lowered = headers.map((h) => h.toLowerCase());
  for (const hint of hints) {
    const idx = lowered.findIndex((h) => h.includes(hint));
    if (idx >= 0) return headers[idx];
  }
  return "";
}

export function suggestMapping(headers: string[], sampleRows: Array<Record<string, string>>): ColumnMapping {
  const date = matchHeader(headers, ["date", "تاريخ", "tarikh"]);
  const description = matchHeader(headers, [
    "description",
    "narration",
    "details",
    "memo",
    "particulars",
    "وصف",
    "البيان",
  ]);
  const reference = matchHeader(headers, ["reference", "ref", "transaction id", "txn", "رقم"]);
  const debit = matchHeader(headers, ["debit", "withdraw", "out", "مدين"]);
  const credit = matchHeader(headers, ["credit", "deposit", "in", "دائن"]);
  let amount = "";
  if (!debit || !credit) {
    amount = matchHeader(headers, ["amount", "value", "قيمة"]);
  }

  // Sniff the date format from ALL sample rows for the chosen date column. When
  // the file is genuinely DD/MM-vs-MM/DD ambiguous this returns "" so the dialog
  // forces an explicit pick instead of silently transposing day/month.
  const dateFormat = date
    ? detectDateFormat(sampleRows.map((r) => String(r?.[date] ?? ""))).format
    : "%Y-%m-%d";

  return {
    date_column: date,
    description_column: description,
    amount_column: amount,
    reference_column: reference,
    debit_column: debit,
    credit_column: credit,
    date_format: dateFormat,
  };
}

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  headers: string[];
  sampleRows: Array<Record<string, string>>;
  initialMapping?: Partial<ColumnMapping>;
  onConfirm: (mapping: ColumnMapping) => void;
};

export function CsvMappingDialog({
  open,
  onOpenChange,
  headers,
  sampleRows,
  initialMapping,
  onConfirm,
}: Props) {
  const suggested = useMemo(
    () => suggestMapping(headers, sampleRows),
    [headers, sampleRows],
  );

  const [mapping, setMapping] = useState<ColumnMapping>(suggested);

  useEffect(() => {
    // Re-seed when the dialog opens (new file uploaded). Last-saved mapping
    // wins over auto-detect, but only for fields the user actually had set.
    if (!open) return;
    const next: ColumnMapping = { ...suggested };
    if (initialMapping) {
      (Object.keys(next) as Array<keyof ColumnMapping>).forEach((k) => {
        const saved = initialMapping[k];
        // Only adopt a saved column if it still exists in this file's
        // headers — schemas drift between exports.
        if (k === "date_format") {
          if (saved) next[k] = saved as string;
        } else if (saved && headers.includes(saved)) {
          next[k] = saved;
        }
      });
    }
    setMapping(next);
  }, [open, initialMapping, suggested, headers]);

  const update = (key: keyof ColumnMapping, value: string) =>
    setMapping((prev) => {
      const v = value === NONE ? "" : value;
      if (key === "date_column") {
        // Re-sniff the format against the newly chosen column's values.
        return {
          ...prev,
          date_column: v,
          date_format: detectDateFormat(sampleRows.map((r) => String(r?.[v] ?? ""))).format,
        };
      }
      return { ...prev, [key]: v };
    });

  const useDebitCredit = Boolean(mapping.debit_column && mapping.credit_column);

  // When the date column's values could be DD/MM or MM/DD and the user hasn't
  // picked yet, block Parse and warn instead of guessing.
  const dateDetection = useMemo(
    () => detectDateFormat(sampleRows.map((r) => String(r?.[mapping.date_column] ?? ""))),
    [sampleRows, mapping.date_column],
  );
  const dateFormatAmbiguous = !mapping.date_format && dateDetection.ambiguous;

  const requiredOK =
    Boolean(mapping.date_column) &&
    Boolean(mapping.date_format) &&
    Boolean(mapping.description_column) &&
    (Boolean(mapping.amount_column) ||
      (Boolean(mapping.debit_column) && Boolean(mapping.credit_column)));

  const renderSelect = (
    fieldKey: keyof ColumnMapping,
    label: string,
    required: boolean,
    helper?: string,
  ) => (
    <div className="space-y-1.5">
      <Label className="text-sm">
        {label}
        {required && <span className="ms-0.5 text-destructive">*</span>}
      </Label>
      <select
        className="w-full border rounded-md px-3 py-2 text-sm bg-background"
        value={mapping[fieldKey] || NONE}
        onChange={(e) => update(fieldKey, e.target.value)}
      >
        <option value={NONE}>(none)</option>
        {headers.map((h) => (
          <option key={h} value={h}>
            {h}
          </option>
        ))}
      </select>
      {helper && <p className="text-xs text-muted-foreground">{helper}</p>}
    </div>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Map CSV columns</DialogTitle>
          <DialogDescription>
            Pick which column in your file carries each field. We&apos;ve guessed
            based on the headers — adjust where wrong, then click Parse.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            {renderSelect("date_column", "Date column", true)}
            <div className="space-y-1.5">
              <Label className="text-sm">
                Date format
                <span className="ms-0.5 text-destructive">*</span>
              </Label>
              <select
                className={`w-full border rounded-md px-3 py-2 text-sm bg-background${
                  dateFormatAmbiguous ? " border-destructive" : ""
                }`}
                value={mapping.date_format}
                onChange={(e) => update("date_format", e.target.value)}
              >
                <option value="">— Select date format —</option>
                {DATE_FORMATS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
              {dateFormatAmbiguous && (
                <p className="text-xs text-destructive">
                  These dates could be DD/MM or MM/DD — we can&apos;t tell which.
                  Pick the correct format; choosing wrong silently swaps the day
                  and month.
                </p>
              )}
            </div>
            {renderSelect("description_column", "Description column", true)}
            {renderSelect(
              "reference_column",
              "Reference column",
              false,
              "Optional. Bank reference / transaction ID.",
            )}
          </div>

          <div className="rounded-md border p-3 space-y-3">
            <p className="text-sm font-medium">Amount</p>
            <p className="text-xs text-muted-foreground">
              Either a single amount column (positive deposit, negative
              withdrawal) OR separate debit/credit columns. Pick one.
            </p>
            <div className="grid gap-4 sm:grid-cols-3">
              {renderSelect(
                "amount_column",
                "Single amount",
                !useDebitCredit,
                "Use this if your file has one signed Amount column.",
              )}
              {renderSelect(
                "debit_column",
                "Debit (withdrawal)",
                false,
                "Use with Credit if your file has two columns.",
              )}
              {renderSelect("credit_column", "Credit (deposit)", false)}
            </div>
          </div>

          {sampleRows.length > 0 && mapping.date_column && (
            <div className="rounded-md border p-3 text-xs">
              <p className="font-medium mb-1">First-row preview</p>
              <p className="text-muted-foreground">
                Date: <span className="font-mono">{String(sampleRows[0][mapping.date_column] ?? "—")}</span>
                {mapping.description_column && (
                  <>
                    {" · "}Desc:{" "}
                    <span className="font-mono">
                      {String(sampleRows[0][mapping.description_column] ?? "—")}
                    </span>
                  </>
                )}
                {mapping.amount_column && (
                  <>
                    {" · "}Amount:{" "}
                    <span className="font-mono">
                      {String(sampleRows[0][mapping.amount_column] ?? "—")}
                    </span>
                  </>
                )}
                {useDebitCredit && (
                  <>
                    {" · "}Debit:{" "}
                    <span className="font-mono">
                      {String(sampleRows[0][mapping.debit_column] ?? "—")}
                    </span>
                    {" / "}Credit:{" "}
                    <span className="font-mono">
                      {String(sampleRows[0][mapping.credit_column] ?? "—")}
                    </span>
                  </>
                )}
              </p>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!requiredOK}
            onClick={() => {
              onConfirm(mapping);
              onOpenChange(false);
            }}
          >
            Parse with these columns
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
