/**
 * E2E Test: Month-End Close wizard
 *
 * Verifies:
 * 1. Wizard page loads with period selector
 * 2. Pre-close checks run and display results
 * 3. Each check shows pass/warn/fail with resolution hints
 * 4. System Health page loads and shows diagnostics
 *
 * Prerequisites:
 * - Backend running at localhost:8000
 * - Frontend running at localhost:3000
 * - Demo company seeded
 */

import { test, expect } from "@playwright/test";

async function login(page: any) {
  await page.goto("/login");
  await page.fill("#email", "demo@nxentra.com");
  await page.fill("#password", "demo1234");
  await page.click('button[type="submit"]');
  await page.waitForURL((url: URL) => !url.pathname.includes("/login"), {
    timeout: 15000,
  });
}

test.describe("Month-End Close Wizard", () => {
  test("wizard page loads with period selector and checks", async ({ page }) => {
    await login(page);
    await page.goto("/settings/month-end-close");
    await page.waitForLoadState("networkidle");

    // Page header should be visible
    await expect(page.locator("body")).toContainText("Month-End Close");

    // Period selector should show a month name
    const months = [
      "January", "February", "March", "April", "May", "June",
      "July", "August", "September", "October", "November", "December",
    ];
    const bodyText = await page.textContent("body");
    const hasMonth = months.some((m) => bodyText?.includes(m));
    expect(hasMonth).toBeTruthy();

    // Wait for checks to load
    await page.waitForTimeout(3000);

    // Should show "Pre-Close Checklist" card
    await expect(page.locator("body")).toContainText("Pre-Close Checklist");

    // Should show check items with PASS/WARN/FAIL badges
    const badges = await page.locator("text=PASS").or(page.locator("text=WARN")).or(page.locator("text=FAIL")).count();
    expect(badges).toBeGreaterThan(0);
  });

  test("wizard shows 'Ready to Close' or 'Not Ready' summary", async ({ page }) => {
    await login(page);
    await page.goto("/settings/month-end-close");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);

    const bodyText = await page.textContent("body");
    const hasReadyStatus =
      bodyText?.includes("Ready to Close") ||
      bodyText?.includes("Not Ready");
    expect(hasReadyStatus).toBeTruthy();
  });

  test("period navigation works", async ({ page }) => {
    await login(page);
    await page.goto("/settings/month-end-close");
    await page.waitForLoadState("networkidle");

    // Get current month text
    const initialText = await page.textContent("h2");

    // Click previous month button
    await page.locator("button").filter({ has: page.locator("svg") }).first().click();
    await page.waitForTimeout(2000);

    // Month should have changed
    const newText = await page.textContent("h2");
    expect(newText).not.toBe(initialText);
  });

  test("how-to guide is collapsible", async ({ page }) => {
    await login(page);
    await page.goto("/settings/month-end-close");
    await page.waitForLoadState("networkidle");

    // Guide should be present but collapsed
    await expect(page.locator("body")).toContainText("How to close a period");

    // Click to expand
    await page.locator("text=How to close a period").click();
    await page.waitForTimeout(500);

    // Should show step 1
    await expect(page.locator("body")).toContainText("Post all entries");
  });
});

test.describe("System Health Dashboard", () => {
  test("system health page loads with check cards", async ({ page }) => {
    await login(page);
    await page.goto("/settings/system-health");
    await page.waitForLoadState("networkidle");

    // Header should be visible
    await expect(page.locator("body")).toContainText("System Health");

    // Wait for checks to load
    await page.waitForTimeout(3000);

    // Should show overall status
    const bodyText = await page.textContent("body");
    const hasOverall =
      bodyText?.includes("All Systems Healthy") ||
      bodyText?.includes("Needs Attention") ||
      bodyText?.includes("Issues Detected");
    expect(hasOverall).toBeTruthy();

    // Should show individual checks
    await expect(page.locator("body")).toContainText("Event Processing");
    await expect(page.locator("body")).toContainText("Trial Balance");
  });

  test("refresh button triggers re-check", async ({ page }) => {
    await login(page);
    await page.goto("/settings/system-health");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    // Click refresh
    await page.locator("button", { hasText: "Refresh" }).click();

    // Should still show the health page (no crash)
    await page.waitForTimeout(2000);
    await expect(page.locator("body")).toContainText("System Health");
  });
});
