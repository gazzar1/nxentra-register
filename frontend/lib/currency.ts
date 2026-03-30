/**
 * Centralized currency formatting utilities.
 *
 * Use these instead of inline Intl.NumberFormat calls to ensure
 * consistent formatting across the application.
 */

export interface CompanyFormatSettings {
  thousand_separator?: string;
  decimal_separator?: string;
  decimal_places?: number;
}

/**
 * Format a number using explicit separator settings from company config.
 */
function formatWithSeparators(
  num: number,
  decimals: number,
  thousandSep: string,
  decimalSep: string,
): string {
  const fixed = Math.abs(num).toFixed(decimals);
  const [intPart, fracPart] = fixed.split(".");
  // Add thousand separators
  const withThousands = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, thousandSep);
  const sign = num < 0 ? "-" : "";
  return fracPart !== undefined
    ? `${sign}${withThousands}${decimalSep}${fracPart}`
    : `${sign}${withThousands}`;
}

/**
 * Format a numeric amount with thousand separators and decimal places.
 * Does NOT include the currency symbol.
 *
 * When `settings` is provided, uses company-configured separators.
 * Otherwise falls back to Intl.NumberFormat with browser locale.
 */
export function formatAmount(
  value: string | number,
  decimalsOrSettings?: number | CompanyFormatSettings,
  settings?: CompanyFormatSettings,
): string {
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "0.00";

  // Resolve overloaded params
  let decimals = 2;
  let fmt: CompanyFormatSettings | undefined;
  if (typeof decimalsOrSettings === "number") {
    decimals = decimalsOrSettings;
    fmt = settings;
  } else if (decimalsOrSettings) {
    fmt = decimalsOrSettings;
    decimals = fmt.decimal_places ?? 2;
  }

  if (fmt) {
    const thousandSep = fmt.thousand_separator ?? ",";
    const decimalSep = fmt.decimal_separator ?? ".";
    decimals = fmt.decimal_places ?? decimals;
    return formatWithSeparators(num, decimals, thousandSep, decimalSep);
  }

  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(num);
}

/**
 * Format a numeric amount with currency symbol.
 *
 * When `settings` is provided, uses company-configured separators.
 */
export function formatCurrency(
  value: string | number,
  currency: string = "USD",
  decimalsOrSettings?: number | CompanyFormatSettings,
  settings?: CompanyFormatSettings,
): string {
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "0.00";

  let decimals = 2;
  let fmt: CompanyFormatSettings | undefined;
  if (typeof decimalsOrSettings === "number") {
    decimals = decimalsOrSettings;
    fmt = settings;
  } else if (decimalsOrSettings) {
    fmt = decimalsOrSettings;
    decimals = fmt.decimal_places ?? 2;
  }

  if (fmt) {
    const formatted = formatAmount(num, decimals, fmt);
    return `${currency} ${formatted}`;
  }

  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(num);
  } catch {
    // Fallback if currency code is invalid
    return `${currency} ${formatAmount(value, decimals)}`;
  }
}

/**
 * Parse a formatted amount string back to a number.
 * Handles thousand separators and various decimal separators.
 */
export function parseAmount(value: string, settings?: CompanyFormatSettings): number {
  if (!value) return 0;
  const thousandSep = settings?.thousand_separator ?? ",";
  const decimalSep = settings?.decimal_separator ?? ".";
  // Remove thousand separators, then normalize decimal separator to "."
  let cleaned = value;
  if (thousandSep) {
    cleaned = cleaned.split(thousandSep).join("");
  }
  if (decimalSep !== ".") {
    cleaned = cleaned.replace(decimalSep, ".");
  }
  cleaned = cleaned.replace(/[^0-9.\-]/g, "");
  const num = parseFloat(cleaned);
  return isNaN(num) ? 0 : num;
}
