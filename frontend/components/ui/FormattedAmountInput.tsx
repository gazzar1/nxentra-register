import * as React from "react";
import { Input } from "./input";
import type { CompanyFormatSettings } from "@/lib/currency";
import { formatAmount, parseAmount } from "@/lib/currency";

/**
 * A numeric input that displays formatted amounts (with thousand separators)
 * when not focused, and shows the raw number when focused for editing.
 *
 * The underlying form value is always a number.
 */

interface FormattedAmountInputProps
  extends Omit<
    React.InputHTMLAttributes<HTMLInputElement>,
    "value" | "onChange" | "type"
  > {
  value: number;
  onChange: (value: number) => void;
  settings?: CompanyFormatSettings;
  decimals?: number;
}

const FormattedAmountInput = React.forwardRef<
  HTMLInputElement,
  FormattedAmountInputProps
>(({ value, onChange, settings, decimals, className, onBlur, onFocus, ...props }, ref) => {
  const [isFocused, setIsFocused] = React.useState(false);
  const [rawText, setRawText] = React.useState("");

  const dp = decimals ?? settings?.decimal_places ?? 2;

  // Formatted display value (with thousand separators)
  const formattedValue = React.useMemo(() => {
    if (value === 0) return "";
    return formatAmount(value, settings ?? dp);
  }, [value, settings, dp]);

  const handleFocus = (e: React.FocusEvent<HTMLInputElement>) => {
    setIsFocused(true);
    // Show raw number for editing (no thousand separators)
    const raw = value === 0 ? "" : String(value);
    setRawText(raw);
    // Select all on next tick so user can overwrite
    setTimeout(() => e.target.select(), 0);
    onFocus?.(e);
  };

  const handleBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    setIsFocused(false);
    // Parse the raw text and emit the number
    if (rawText === "" || rawText === "-") {
      onChange(0);
    } else {
      const parsed = settings
        ? parseAmount(rawText, settings)
        : parseFloat(rawText) || 0;
      onChange(parsed);
    }
    onBlur?.(e);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    // Allow digits, decimal point, minus sign
    if (/^-?\d*\.?\d*$/.test(val) || val === "") {
      setRawText(val);
      // Also update the form value in real-time for total calculations
      const num = parseFloat(val) || 0;
      onChange(num);
    }
  };

  return (
    <Input
      ref={ref}
      type="text"
      inputMode="decimal"
      value={isFocused ? rawText : formattedValue}
      onChange={handleChange}
      onFocus={handleFocus}
      onBlur={handleBlur}
      className={className}
      {...props}
    />
  );
});
FormattedAmountInput.displayName = "FormattedAmountInput";

export { FormattedAmountInput };
