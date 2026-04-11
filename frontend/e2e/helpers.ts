/**
 * Shared E2E test helpers.
 *
 * Nxentra uses HttpOnly cookies for auth — these can't be saved via
 * Playwright's storageState. Each test must login within its own
 * browser context.
 */

import { type Page } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

/**
 * Login and navigate to a target page.
 * Combines login + navigation in one step to avoid the HttpOnly cookie issue.
 */
export async function loginAndGo(page: Page, targetPath: string) {
  // Login
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Wait for redirect
  await page.waitForURL((url) => !url.pathname.includes("/login"), {
    timeout: 30000,
  });

  // Handle select-company if needed
  if (page.url().includes("/select-company")) {
    await page.waitForTimeout(1000);
    const card = page.locator("button, a").first();
    if (await card.isVisible({ timeout: 3000 }).catch(() => false)) {
      await card.click();
    }
    await page.waitForTimeout(2000);
  }

  // Now navigate to target — cookies are set in this context
  await page.goto(targetPath);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(2000);
}
