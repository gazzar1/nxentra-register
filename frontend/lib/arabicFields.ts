// A138 — single source of truth for "should the optional Arabic data-entry
// fields be shown?". This is a per-company UI preference (Company.enable_arabic_fields),
// deliberately separate from the interface language (which is route/next-i18next
// driven). A company can run an English UI yet still want Arabic invoice/customer
// names — and vice versa.
//
// Use the `useArabicFields()` hook in components; this pure helper exists so the
// rule lives in exactly one place (and is unit-testable without React).

interface HasArabicFieldsSetting {
  enable_arabic_fields?: boolean | null;
}

/**
 * Returns true only when the company has explicitly enabled Arabic data-entry
 * fields. Defaults to false (English-first) when the company or the flag is
 * absent — e.g. a stale cached company object from before the feature shipped.
 * Existing companies are backfilled to `true` server-side, so a real logged-in
 * company always reflects its true preference.
 */
export function shouldShowArabicFields(
  company: HasArabicFieldsSetting | null | undefined
): boolean {
  return company?.enable_arabic_fields === true;
}
