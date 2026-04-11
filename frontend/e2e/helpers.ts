/**
 * Shared E2E test helpers.
 *
 * Auth: HttpOnly JWT cookies + localStorage "nxentra_authenticated" flag.
 *
 * Key insight: after login, cookies are set. But page.goto() does a full
 * document navigation which Playwright tracks. We need to verify the
 * cookies persist by checking the browser context directly.
 */

import { type Page, expect } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

/**
 * Login and navigate to a target page.
 *
 * After login, we set the localStorage auth flag manually (since Playwright
 * can't read HttpOnly cookies) and then navigate. The AuthContext checks
 * this flag + validates via /api/auth/me/ on page load.
 */
export async function loginAndGo(page: Page, targetPath: string) {
  // Step 1: Go to login page
  await page.goto("/login");
  await page.waitForLoadState("networkidle");

  // Step 2: Fill and submit
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Step 3: Wait for successful login (redirect away from /login)
  await page.waitForURL((url) => !url.pathname.includes("/login"), {
    timeout: 30000,
  });

  // Step 4: Now we're logged in. The cookies are set on this browser context.
  // Verify by checking localStorage was set by the login flow
  await page.waitForTimeout(1000);
  const authFlag = await page.evaluate(() => localStorage.getItem("nxentra_authenticated"));

  if (authFlag !== "true") {
    // Login flow didn't set the flag — set it manually
    // (The HttpOnly cookies are already set by the backend response)
    await page.evaluate(() => localStorage.setItem("nxentra_authenticated", "true"));
  }

  // Step 5: Handle select-company if needed
  if (page.url().includes("/select-company")) {
    await page.waitForTimeout(1000);
    const firstCard = page.locator("button, a, [class*='card']").first();
    if (await firstCard.isVisible({ timeout: 3000 }).catch(() => false)) {
      await firstCard.click();
      await page.waitForTimeout(3000);
    }
  }

  // Step 6: Navigate to target page
  // Use page.goto with the relative path — Playwright's baseURL will resolve it
  // Cookies are already in the browser context from step 3
  await page.goto(targetPath);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(3000);
}
