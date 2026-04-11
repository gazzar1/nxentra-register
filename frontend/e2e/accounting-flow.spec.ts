/**
 * E2E Test: Critical accounting flow
 */

import { test, expect } from "@playwright/test";
import { loginAndGo } from "./helpers";

test.describe("Accounting Flow", () => {
  test("journal entries page loads", async ({ page }) => {
    await loginAndGo(page, "/accounting/journal-entries");
    await expect(page.locator("body")).toContainText("Journal Entries");
  });

  test("trial balance page loads", async ({ page }) => {
    await loginAndGo(page, "/reports/trial-balance");
    await expect(page.locator("body")).toContainText("Trial Balance");
  });

  test("new journal entry page loads", async ({ page }) => {
    await loginAndGo(page, "/accounting/journal-entries/new");
    await expect(page.locator("body")).toContainText("New Journal Entry");
  });

  test("chart of accounts page loads", async ({ page }) => {
    await loginAndGo(page, "/accounting/chart-of-accounts");
    await expect(page.locator("body")).toContainText("Chart of Accounts");
  });
});
