/**
 * Shared E2E test helpers.
 *
 * Nxentra uses HttpOnly cookies for auth. After login, we must navigate
 * using client-side routing (window.location) instead of page.goto()
 * to preserve cookies within the same browsing context.
 */

import { type Page } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

/**
 * Login and navigate to a target page using client-side navigation.
 */
export async function loginAndGo(page: Page, targetPath: string) {
  // Login
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Wait for redirect away from login
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

  // Navigate using client-side routing to preserve cookies
  await page.evaluate((path) => {
    window.location.href = path;
  }, targetPath);

  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(3000);
}
