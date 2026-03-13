/**
 * Centralized currency formatting utilities.
 *
 * Use these instead of inline Intl.NumberFormat calls to ensure
 * consistent formatting across the application.
 */

/**
 * Format a numeric amount with thousand separators and decimal places.
 * Does NOT include the currency symbol.
 */
export function formatAmount(
  value: string | number,
  decimals: number = 2,
): string {
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "0.00";
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(num);
}

/**
 * Format a numeric amount with currency symbol.
 */
export function formatCurrency(
  value: string | number,
  currency: string = "USD",
  decimals: number = 2,
): string {
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "0.00";
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
export function parseAmount(value: string): number {
  if (!value) return 0;
  // Remove all non-numeric characters except minus and period
  const cleaned = value.replace(/[^0-9.\-]/g, "");
  const num = parseFloat(cleaned);
  return isNaN(num) ? 0 : num;
}
