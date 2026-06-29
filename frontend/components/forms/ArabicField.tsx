import { ReactNode } from "react";
import { useArabicFields } from "@/hooks/useArabicFields";

interface ArabicFieldProps {
  children: ReactNode;
  /**
   * Escape hatch: force-show regardless of company preference (e.g. inside the
   * onboarding step where the user is actively choosing the bilingual option and
   * the Arabic name input should appear immediately on that choice).
   */
  show?: boolean;
}

/**
 * A138 — renders its children only when the company has enabled optional Arabic
 * data-entry fields (or when `show` is explicitly passed). When hidden it renders
 * nothing, so the surrounding flex/grid layout simply closes the gap — no empty
 * placeholder is left behind.
 *
 * IMPORTANT: hiding is purely visual. The underlying form field keeps its value
 * (react-hook-form retains unmounted field values by default; useState-backed
 * forms keep the value initialized from the loaded record), so existing Arabic
 * data round-trips back to the API unchanged on save.
 */
export function ArabicField({ children, show }: ArabicFieldProps) {
  const enabled = useArabicFields();
  if (!(show ?? enabled)) return null;
  return <>{children}</>;
}
