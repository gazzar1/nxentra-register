// RecordNavigator — prev/next chevron pair + "N of M" position indicator
// for record-detail/edit pages. Reads the same list hook the list page
// uses (already cached by React Query if the user navigated from there).
//
// Keyboard shortcuts: ← previous, → next. Gated on the focused element
// NOT being an input/textarea/select/combobox so arrow keys still move
// the cursor inside text fields and dropdown listboxes.
//
// Records are addressed by an arbitrary key (typically `id` or `code`)
// that the consumer extracts via `getKey`. The hrefBuilder turns a key
// into a destination path (defaults to `${basePath}/${key}/edit`).

import { useEffect } from "react";
import { useRouter } from "next/router";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

interface RecordNavigatorProps<T> {
  records: T[] | undefined;
  currentKey: string | number | null | undefined;
  getKey: (record: T) => string | number;
  basePath: string;
  // Display label for tooltips (e.g., `INV-000001 - Cairo Retail Group`).
  // Defaults to the key itself.
  getLabel?: (record: T) => string;
  // Override the destination URL builder. Defaults to `${basePath}/${key}/edit`.
  hrefBuilder?: (key: string | number) => string;
}

const EDITABLE_SELECTOR =
  "input, textarea, select, [contenteditable=true], [role='combobox'], [role='listbox'], [role='option']";

export function RecordNavigator<T>({
  records,
  currentKey,
  getKey,
  basePath,
  getLabel,
  hrefBuilder,
}: RecordNavigatorProps<T>) {
  const router = useRouter();

  const buildHref =
    hrefBuilder ?? ((k: string | number) => `${basePath}/${k}/edit`);

  const currentIndex =
    currentKey != null && records
      ? records.findIndex((r) => String(getKey(r)) === String(currentKey))
      : -1;

  const prev = currentIndex > 0 ? records?.[currentIndex - 1] : undefined;
  const next =
    currentIndex >= 0 && records && currentIndex < records.length - 1
      ? records[currentIndex + 1]
      : undefined;

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const target = e.target as HTMLElement | null;
      if (target?.matches(EDITABLE_SELECTOR)) return;
      if (e.key === "ArrowLeft" && prev) {
        e.preventDefault();
        router.push(buildHref(getKey(prev)));
      } else if (e.key === "ArrowRight" && next) {
        e.preventDefault();
        router.push(buildHref(getKey(next)));
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prev, next, router]);

  const prevLabel = prev
    ? (getLabel ? getLabel(prev) : String(getKey(prev)))
    : "";
  const nextLabel = next
    ? (getLabel ? getLabel(next) : String(getKey(next)))
    : "";

  return (
    <div className="flex items-center gap-1 me-2">
      <Button
        type="button"
        variant="outline"
        size="icon"
        disabled={!prev}
        title={prev ? `Previous: ${prevLabel}  (←)` : "No previous record"}
        onClick={() => prev && router.push(buildHref(getKey(prev)))}
      >
        <ChevronLeft className="h-4 w-4" />
      </Button>
      {records && currentIndex >= 0 && (
        <span className="text-xs text-muted-foreground tabular-nums px-1 min-w-[3.5rem] text-center">
          {currentIndex + 1} of {records.length}
        </span>
      )}
      <Button
        type="button"
        variant="outline"
        size="icon"
        disabled={!next}
        title={next ? `Next: ${nextLabel}  (→)` : "No next record"}
        onClick={() => next && router.push(buildHref(getKey(next)))}
      >
        <ChevronRight className="h-4 w-4" />
      </Button>
    </div>
  );
}
