/**
 * Shared E2E test helpers.
 *
 * Nxentra uses HttpOnly Secure cookies. page.goto() after login loses
 * cookies because Playwright treats each navigation as potentially
 * cross-origin. Solution: navigate by typing in the address bar within
 * the same page, which preserves the browsing context.
 */

import { type Page } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

/**
 * Login then navigate to target by changing location within the same tab.
 */
export async function loginAndGo(page: Page, targetPath: string) {
  // Login
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);

  // Submit and wait for redirect
  await Promise.all([
    page.waitForNavigation({ timeout: 30000 }),
    page.click('button[type="submit"]'),
  ]);

  await page.waitForTimeout(2000);

  // Handle select-company
  if (page.url().includes("/select-company")) {
    const card = page.locator("button, a").first();
    if (await card.isVisible({ timeout: 3000 }).catch(() => false)) {
      await Promise.all([
        page.waitForNavigation({ timeout: 15000 }).catch(() => {}),
        card.click(),
      ]);
      await page.waitForTimeout(2000);
    }
  }

  // Navigate to target using the address bar (same browsing context)
  const baseUrl = page.url().split("/").slice(0, 3).join("/"); // e.g. https://app.nxentra.com
  await page.evaluate((url) => {
    window.location.replace(url);
  }, baseUrl + targetPath);

  // Wait for page to load
  await page.waitForLoadState("load");
  await page.waitForTimeout(4000);
}
