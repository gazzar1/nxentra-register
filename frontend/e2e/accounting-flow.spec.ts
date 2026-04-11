/**
 * E2E Test: Critical accounting flow
 *
 * Verifies journal entries page, trial balance, and new entry form load correctly.
 * This is the existential risk test — if these pages break, the product is broken.
 */

import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test.describe("Accounting Flow", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("journal entries page loads", async ({ page }) => {
    await page.goto("/accounting/journal-entries");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);
    await expect(page.locator("body")).toContainText("Journal Entries");
  });

  test("trial balance page loads", async ({ page }) => {
    await page.goto("/reports/trial-balance");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);
    await expect(page.locator("body")).toContainText("Trial Balance");
  });

  test("new journal entry page loads", async ({ page }) => {
    await page.goto("/accounting/journal-entries/new");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);
    await expect(page.locator("body")).toContainText("New Journal Entry");
  });

  test("chart of accounts page loads", async ({ page }) => {
    await page.goto("/accounting/chart-of-accounts");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);
    await expect(page.locator("body")).toContainText("Chart of Accounts");
  });
});
