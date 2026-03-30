import * as React from "react";
import { Input } from "./input";

/**
 * A date input that displays and accepts dates in the company's date format
 * (e.g., DD/MM/YYYY) while storing the value internally as YYYY-MM-DD.
 *
 * Falls back to YYYY-MM-DD if no format is specified.
 */

export type DateFormat =
  | "YYYY-MM-DD"
  | "DD/MM/YYYY"
  | "MM/DD/YYYY"
  | "DD-MM-YYYY"
  | "DD.MM.YYYY";

interface CompanyDateInputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> {
  /** ISO date string (YYYY-MM-DD) */
  value: string;
  /** Called with ISO date string (YYYY-MM-DD) */
  onChange: (isoDate: string) => void;
  dateFormat?: DateFormat;
}

function isoToDisplay(iso: string, fmt: DateFormat): string {
  if (!iso) return "";
  const [yyyy, mm, dd] = iso.split("-");
  if (!yyyy || !mm || !dd) return iso;
  switch (fmt) {
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
      return iso;
  }
}

function displayToIso(display: string, fmt: DateFormat): string {
  if (!display) return "";
  // Extract separator from format
  const sep = fmt.includes("/") ? "/" : fmt.includes(".") ? "." : "-";
  const parts = display.split(sep);
  if (parts.length !== 3) return "";

  let yyyy: string, mm: string, dd: string;
  switch (fmt) {
    case "DD/MM/YYYY":
    case "DD-MM-YYYY":
    case "DD.MM.YYYY":
      [dd, mm, yyyy] = parts;
      break;
    case "MM/DD/YYYY":
      [mm, dd, yyyy] = parts;
      break;
    case "YYYY-MM-DD":
    default:
      [yyyy, mm, dd] = parts;
      break;
  }

  // Validate
  const y = parseInt(yyyy), m = parseInt(mm), d = parseInt(dd);
  if (isNaN(y) || isNaN(m) || isNaN(d)) return "";
  if (m < 1 || m > 12 || d < 1 || d > 31) return "";

  return `${yyyy.padStart(4, "0")}-${mm.padStart(2, "0")}-${dd.padStart(2, "0")}`;
}

function getPlaceholder(fmt: DateFormat): string {
  return fmt;
}

function getSeparator(fmt: DateFormat): string {
  return fmt.includes("/") ? "/" : fmt.includes(".") ? "." : "-";
}

const CompanyDateInput = React.forwardRef<HTMLInputElement, CompanyDateInputProps>(
  ({ value, onChange, dateFormat = "YYYY-MM-DD", className, onBlur, ...props }, ref) => {
    const [displayValue, setDisplayValue] = React.useState(() =>
      isoToDisplay(value, dateFormat)
    );
    const [isFocused, setIsFocused] = React.useState(false);

    // Sync display value when the external value changes (and we're not editing)
    React.useEffect(() => {
      if (!isFocused) {
        setDisplayValue(isoToDisplay(value, dateFormat));
      }
    }, [value, dateFormat, isFocused]);

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      let input = e.target.value;
      const sep = getSeparator(dateFormat);

      // Auto-insert separators as user types digits
      const digitsOnly = input.replace(/\D/g, "");
      if (digitsOnly.length <= 8) {
        let formatted = "";
        if (dateFormat === "YYYY-MM-DD") {
          // YYYY-MM-DD
          for (let i = 0; i < digitsOnly.length; i++) {
            if (i === 4 || i === 6) formatted += sep;
            formatted += digitsOnly[i];
          }
        } else {
          // DD/MM/YYYY or MM/DD/YYYY or DD-MM-YYYY or DD.MM.YYYY
          for (let i = 0; i < digitsOnly.length; i++) {
            if (i === 2 || i === 4) formatted += sep;
            formatted += digitsOnly[i];
          }
        }
        input = formatted;
      }

      setDisplayValue(input);

      // Try to parse and emit ISO value
      const iso = displayToIso(input, dateFormat);
      if (iso) {
        onChange(iso);
      }
    };

    const handleFocus = () => {
      setIsFocused(true);
    };

    const handleBlur = (e: React.FocusEvent<HTMLInputElement>) => {
      setIsFocused(false);
      // Reformat on blur
      const iso = displayToIso(displayValue, dateFormat);
      if (iso) {
        setDisplayValue(isoToDisplay(iso, dateFormat));
        onChange(iso);
      }
      onBlur?.(e);
    };

    return (
      <Input
        ref={ref}
        type="text"
        inputMode="numeric"
        placeholder={getPlaceholder(dateFormat)}
        value={displayValue}
        onChange={handleChange}
        onFocus={handleFocus}
        onBlur={handleBlur}
        className={className}
        maxLength={10}
        {...props}
      />
    );
  }
);
CompanyDateInput.displayName = "CompanyDateInput";

export { CompanyDateInput, isoToDisplay, displayToIso };
