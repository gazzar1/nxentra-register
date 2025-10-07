export const currencyOptions = [
  "USD",
  "EUR",
  "GBP",
  "AED",
  "SAR",
  "EGP",
  "KWD",
  "QAR",
  "OMR"
];

export const languageOptions = [
  { value: "en", label: "English" },
  { value: "ar", label: "Arabic" }
];

export const thousandSeparators = [",", ".", "none"] as const;
export const decimalSeparators = [".", ","] as const;
export const decimalPlaces = ["0", "1", "2", "3", "4"] as const;
export const dateFormats = ["dd/mm/yyyy", "mm/dd/yyyy", "yyyy/mm/dd"] as const;

export const accountingPeriods = [
  { value: "12", label: "12 (Monthly)" },
  { value: "4", label: "4 (Quarterly)" },
  { value: "1", label: "1 (Yearly)" }
];
