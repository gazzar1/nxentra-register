import { useEffect, useCallback, RefObject } from "react";

interface FormKeyboardShortcutsOptions {
  formRef: RefObject<HTMLFormElement | HTMLDivElement | null>;
  onSave?: () => void;
  onSubmit?: () => void;
  onCancel?: () => void;
  enabled?: boolean;
}

/**
 * Keyboard shortcuts for form navigation and actions.
 *
 * - Enter         → focus next input/select/textarea
 * - Shift+Enter   → focus previous input/select/textarea
 * - Ctrl+Enter    → submit/post
 * - Ctrl+S        → save
 * - Escape        → cancel
 */
export function useFormKeyboardShortcuts({
  formRef,
  onSave,
  onSubmit,
  onCancel,
  enabled = true,
}: FormKeyboardShortcutsOptions) {
  const getFocusableFields = useCallback((): HTMLElement[] => {
    if (!formRef.current) return [];
    const selectors = [
      "input:not([disabled]):not([type=hidden])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "[role=combobox]:not([disabled])",
    ].join(",");
    return Array.from(formRef.current.querySelectorAll<HTMLElement>(selectors));
  }, [formRef]);

  useEffect(() => {
    if (!enabled) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;

      // Ignore if inside a dropdown/popover overlay (radix select content)
      if (target.closest("[data-radix-popper-content-wrapper]")) return;

      // Ctrl+S → save
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        onSave?.();
        return;
      }

      // Ctrl+Enter → submit/post
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        onSubmit?.();
        return;
      }

      // Escape → cancel
      if (e.key === "Escape") {
        // Don't cancel if a select dropdown is open
        if (document.querySelector("[data-radix-popper-content-wrapper]")) return;
        e.preventDefault();
        onCancel?.();
        return;
      }

      // Enter / Shift+Enter → navigate fields
      if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) {
        // Allow Enter in textareas for newlines
        if (target.tagName === "TEXTAREA") return;
        // Don't interfere with buttons
        if (target.tagName === "BUTTON") return;
        // Don't interfere with select triggers (let them open)
        if (target.closest("[role=combobox]")) return;

        e.preventDefault();

        const fields = getFocusableFields();
        const currentIndex = fields.indexOf(target);
        if (currentIndex === -1) return;

        if (e.shiftKey) {
          // Shift+Enter → previous field
          const prevIndex = currentIndex - 1;
          if (prevIndex >= 0) {
            fields[prevIndex].focus();
          }
        } else {
          // Enter → next field
          const nextIndex = currentIndex + 1;
          if (nextIndex < fields.length) {
            fields[nextIndex].focus();
          }
        }
      }
    };

    const container = formRef.current;
    if (!container) return;

    container.addEventListener("keydown", handleKeyDown);
    return () => container.removeEventListener("keydown", handleKeyDown);
  }, [enabled, formRef, onSave, onSubmit, onCancel, getFocusableFields]);
}
