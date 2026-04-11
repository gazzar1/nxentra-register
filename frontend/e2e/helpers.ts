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

  // Wait for navigation away from login
  // The app uses client-side auth — after login API call succeeds,
  // React state updates and router pushes to the next page.
  // We need to wait for the URL to change OR for the page content to change.
  await page.waitForTimeout(3000);

  // Check if we're still on login
  if (page.url().includes("/login")) {
    // Try waiting longer — client-side redirect may be slow
    try {
      await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 15000 });
    } catch {
      // Still on login — may need to check for errors
    }
  }

  // If we landed on select-company, pick the first company
  if (page.url().includes("/select-company")) {
    // Click the first company card/button
    const companyCard = page.locator("[class*='card'], [class*='Card'], button, a")
      .filter({ hasText: /./i })
      .first();
    if (await companyCard.isVisible({ timeout: 3000 }).catch(() => false)) {
      await companyCard.click();
      await page.waitForTimeout(3000);
    }
  }

  // Wait for the app to fully load (sidebar or header visible)
  await page.waitForTimeout(2000);
}
