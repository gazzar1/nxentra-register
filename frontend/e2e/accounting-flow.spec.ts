/**
 * E2E Test: Critical accounting flow
 *
 * Auth state is pre-loaded from auth.setup.ts — no login needed per test.
 */

import { test, expect } from "@playwright/test";

test.describe("Accounting Flow", () => {
  test("journal entries page loads", async ({ page }) => {
    await page.goto("/accounting/journal-entries");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);
    await expect(page.locator("body")).toContainText("Journal Entries");
  });

  test("trial balance page loads", async ({ page }) => {
    await page.goto("/reports/trial-balance");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);
    await expect(page.locator("body")).toContainText("Trial Balance");
  });

  test("new journal entry page loads", async ({ page }) => {
    await page.goto("/accounting/journal-entries/new");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);
    await expect(page.locator("body")).toContainText("New Journal Entry");
  });

  test("chart of accounts page loads", async ({ page }) => {
    await page.goto("/accounting/chart-of-accounts");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);
    await expect(page.locator("body")).toContainText("Chart of Accounts");
  });
});
