/**
 * Shared E2E test helpers.
 *
 * Login credentials can be set via environment variables:
 *   E2E_EMAIL=demo@nxentra.com
 *   E2E_PASSWORD=demo1234
 *
 * Defaults to demo credentials if not set.
 */

import { type Page, expect } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

/**
 * Login and wait for the app to load.
 * Handles all post-login states: dashboard, select-company, verify-email, pending-approval.
 */
export async function login(page: Page) {
  await page.goto("/login");
  await page.waitForLoadState("networkidle");

  // Fill and submit
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Wait for any navigation away from login, or for an error to appear
  try {
    await page.waitForURL(
      (url) => {
        const path = url.pathname;
        return (
          !path.includes("/login") ||
          path.includes("/dashboard") ||
          path.includes("/select-company") ||
          path.includes("/verify-email") ||
          path.includes("/pending-approval") ||
          path.includes("/onboarding")
        );
      },
      { timeout: 30000 }
    );
  } catch {
    // If redirect didn't happen, check if there's an error on the page
    const bodyText = await page.textContent("body");
    if (bodyText?.includes("Invalid") || bodyText?.includes("error") || bodyText?.includes("incorrect")) {
      throw new Error(`Login failed — check credentials (${E2E_EMAIL}). Page shows: ${bodyText?.substring(0, 200)}`);
    }
    // May still be on login due to slow redirect — continue and let the test handle it
  }

  // If we landed on select-company, pick the first one
  if (page.url().includes("/select-company")) {
    const firstCompany = page.locator("button, a, [role='button']").filter({ hasText: /select|choose|enter/i }).first();
    if (await firstCompany.isVisible({ timeout: 3000 }).catch(() => false)) {
      await firstCompany.click();
      await page.waitForTimeout(2000);
    }
  }
}
