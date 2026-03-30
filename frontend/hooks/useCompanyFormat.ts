import { useCallback } from "react";
import { useAuth } from "@/contexts/AuthContext";
import {
  formatAmount,
  formatCurrency as fmtCurrency,
  parseAmount,
  type CompanyFormatSettings,
} from "@/lib/currency";

/**
 * Hook that returns company-aware formatting functions.
 *
 * Uses the company's thousand_separator, decimal_separator, decimal_places,
 * and date_format settings from the auth context.
 */
export function useCompanyFormat() {
  const { company } = useAuth();

  const settings: CompanyFormatSettings | undefined = company
    ? {
        thousand_separator: company.thousand_separator,
        decimal_separator: company.decimal_separator,
        decimal_places: company.decimal_places,
      }
    : undefined;

  const dateFormat = company?.date_format || "YYYY-MM-DD";

  const fmtAmount = useCallback(
    (value: string | number, decimals?: number) => {
      if (settings) {
        const s = decimals !== undefined
          ? { ...settings, decimal_places: decimals }
          : settings;
        return formatAmount(value, s);
      }
      return formatAmount(value, decimals ?? 2);
    },
    [settings]
  );

  const fmtCurrencyFn = useCallback(
    (value: string | number, currency?: string, decimals?: number) => {
      const cur = currency || company?.default_currency || "USD";
      if (settings) {
        const s = decimals !== undefined
          ? { ...settings, decimal_places: decimals }
          : settings;
        return fmtCurrency(value, cur, s);
      }
      return fmtCurrency(value, cur, decimals ?? 2);
    },
    [settings, company?.default_currency]
  );

  const parseAmountFn = useCallback(
    (value: string) => parseAmount(value, settings),
    [settings]
  );

  /**
   * Format a Date or ISO string according to company date_format.
   * Supports: YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY, DD-MM-YYYY, DD.MM.YYYY
   */
  const fmtDate = useCallback(
    (value: string | Date) => {
      const d = typeof value === "string" ? new Date(value) : value;
      if (isNaN(d.getTime())) return String(value);

      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      const dd = String(d.getDate()).padStart(2, "0");

      switch (dateFormat) {
        case "DD/MM/YYYY":
          return `${dd}/${mm}/${yyyy}`;
        case "MM/DD/YYYY":
          return `${mm}/${dd}/${yyyy}`;
        case "DD-MM-YYYY":
          return `${dd}-${mm}-${yyyy}`;
        case "DD.MM.YYYY":
          return `${dd}.${mm}.${yyyy}`;
        case "YYYY-MM-DD":
        default:
          return `${yyyy}-${mm}-${dd}`;
      }
    },
    [dateFormat]
  );

  return {
    formatAmount: fmtAmount,
    formatCurrency: fmtCurrencyFn,
    parseAmount: parseAmountFn,
    formatDate: fmtDate,
    settings,
    dateFormat,
  };
}
