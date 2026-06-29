import { useAuth } from "@/contexts/AuthContext";
import { shouldShowArabicFields } from "@/lib/arabicFields";

/**
 * A138 — whether optional Arabic data-entry fields should be rendered for the
 * current company. Reads the company from auth context and delegates the rule
 * to shouldShowArabicFields(). Returns false (English-first) until the company
 * has loaded or when Arabic fields are disabled.
 */
export function useArabicFields(): boolean {
  const { company } = useAuth();
  return shouldShowArabicFields(company);
}
