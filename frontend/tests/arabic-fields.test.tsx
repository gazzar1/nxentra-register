/**
 * A138 — optional Arabic data-entry field visibility.
 *
 * Covers the centralized helper, the <ArabicField> wrapper, and a real form
 * (AccountForm): Arabic inputs are hidden for an English-only company and shown
 * for an Arabic-enabled one, the English fields always render, and toggling the
 * company preference flips visibility.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { shouldShowArabicFields } from "@/lib/arabicFields";

// ── Mutable mocked company (toggled per test) ────────────────────
let mockCompany: { enable_arabic_fields?: boolean } | null = { enable_arabic_fields: false };
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ company: mockCompany }),
}));

vi.mock("next-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback || key.split(".").pop() || key,
  }),
}));

// AccountForm reads the chart of accounts for the parent dropdown.
vi.mock("@/queries/useAccounts", () => ({
  useAccounts: () => ({ data: [] }),
}));

import { ArabicField } from "@/components/forms/ArabicField";
import { AccountForm } from "@/components/forms/AccountForm";

describe("shouldShowArabicFields", () => {
  it("is false when the company or flag is missing", () => {
    expect(shouldShowArabicFields(null)).toBe(false);
    expect(shouldShowArabicFields(undefined)).toBe(false);
    expect(shouldShowArabicFields({})).toBe(false);
    expect(shouldShowArabicFields({ enable_arabic_fields: false })).toBe(false);
  });

  it("is true only when explicitly enabled", () => {
    expect(shouldShowArabicFields({ enable_arabic_fields: true })).toBe(true);
  });
});

describe("ArabicField wrapper", () => {
  beforeEach(() => {
    mockCompany = { enable_arabic_fields: false };
  });

  it("hides children when Arabic fields are disabled", () => {
    mockCompany = { enable_arabic_fields: false };
    render(
      <ArabicField>
        <span>arabic-input</span>
      </ArabicField>
    );
    expect(screen.queryByText("arabic-input")).not.toBeInTheDocument();
  });

  it("shows children when Arabic fields are enabled", () => {
    mockCompany = { enable_arabic_fields: true };
    render(
      <ArabicField>
        <span>arabic-input</span>
      </ArabicField>
    );
    expect(screen.getByText("arabic-input")).toBeInTheDocument();
  });

  it("the show prop forces children even when disabled", () => {
    mockCompany = { enable_arabic_fields: false };
    render(
      <ArabicField show>
        <span>forced-input</span>
      </ArabicField>
    );
    expect(screen.getByText("forced-input")).toBeInTheDocument();
  });
});

describe("AccountForm Arabic fields", () => {
  it("hides the Arabic name input for an English-only company but keeps English fields", () => {
    mockCompany = { enable_arabic_fields: false };
    render(<AccountForm onSubmit={vi.fn()} />);
    // Arabic name input (placeholder النقدية) must be hidden...
    expect(screen.queryByPlaceholderText("النقدية")).not.toBeInTheDocument();
    // ...while the English code/name inputs still render (no broken layout).
    expect(screen.getByPlaceholderText("Cash")).toBeInTheDocument();
  });

  it("shows the Arabic name input when the company enabled Arabic fields", () => {
    mockCompany = { enable_arabic_fields: true };
    render(<AccountForm onSubmit={vi.fn()} />);
    expect(screen.getByPlaceholderText("النقدية")).toBeInTheDocument();
  });
});
