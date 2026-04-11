/**
 * E2E Test: Month-End Close wizard and System Health dashboard
 */

import { test, expect } from "@playwright/test";
import { loginAndGo } from "./helpers";

test.describe("Month-End Close Wizard", () => {
  test("wizard page loads with period selector and checks", async ({ page }) => {
    await loginAndGo(page, "/settings/month-end-close");

    await expect(page.locator("body")).toContainText("Month-End Close");
    await expect(page.locator("body")).toContainText("Pre-Close Checklist");
  });

  test("wizard shows Ready or Not Ready summary", async ({ page }) => {
    await loginAndGo(page, "/settings/month-end-close");
    await page.waitForTimeout(2000);

    const bodyText = await page.textContent("body");
    expect(
      bodyText?.includes("Ready to Close") || bodyText?.includes("Not Ready")
    ).toBeTruthy();
  });

  test("how-to guide is collapsible", async ({ page }) => {
    await loginAndGo(page, "/settings/month-end-close");

    await expect(page.locator("body")).toContainText("How to close a period");
    await page.locator("text=How to close a period").click();
    await page.waitForTimeout(500);
    await expect(page.locator("body")).toContainText("Post all entries");
  });
});

test.describe("System Health Dashboard", () => {
  test("system health page loads with check cards", async ({ page }) => {
    await loginAndGo(page, "/settings/system-health");

    await expect(page.locator("body")).toContainText("System Health");

    const bodyText = await page.textContent("body");
    expect(
      bodyText?.includes("All Systems Healthy") ||
      bodyText?.includes("Needs Attention") ||
      bodyText?.includes("Issues Detected")
    ).toBeTruthy();
  });

  test("refresh button works", async ({ page }) => {
    await loginAndGo(page, "/settings/system-health");

    await page.locator("button", { hasText: "Refresh" }).click();
    await page.waitForTimeout(2000);
    await expect(page.locator("body")).toContainText("System Health");
  });
});
