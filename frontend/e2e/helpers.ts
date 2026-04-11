/**
 * Shared E2E test helpers.
 *
 * Auth diagnosis (from debug-auth.spec.ts):
 * - Login succeeds (redirects to /select-company)
 * - HttpOnly cookies are set but invisible to Playwright (expected)
 * - localStorage "nxentra_authenticated" is NOT set (React hasn't hydrated)
 * - Fix: manually set the flag after login redirect
 */

import { type Page } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

export async function loginAndGo(page: Page, targetPath: string) {
  // Login
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Wait for login redirect (to /select-company, /dashboard, etc.)
  await page.waitForURL((url) => !url.pathname.includes("/login"), {
    timeout: 30000,
  });

  // Set the auth flag that React's AuthContext expects
  // (The login API set HttpOnly cookies, but the React hydration
  // that would normally call setAuthenticated(true) hasn't completed)
  await page.evaluate(() => {
    localStorage.setItem("nxentra_authenticated", "true");
  });

  // Handle /select-company — need to wait for React to render
  if (page.url().includes("/select-company")) {
    // Wait for the page to actually render company cards
    await page.waitForTimeout(3000);

    // Click the first company card/button/link
    const companyEl = page.locator("a, button, [role='button'], [class*='card']")
      .filter({ hasNotText: /sign|login|register|get started/i })
      .first();

    if (await companyEl.isVisible({ timeout: 5000 }).catch(() => false)) {
      await companyEl.click();
      await page.waitForTimeout(3000);
    } else {
      // If no company card visible, try reloading with auth flag set
      await page.reload();
      await page.waitForTimeout(3000);
    }
  }

  // Ensure auth flag is still set (page transitions may clear it)
  await page.evaluate(() => {
    localStorage.setItem("nxentra_authenticated", "true");
  });

  // Navigate to target
  await page.goto(targetPath, { waitUntil: "networkidle" });
  await page.waitForTimeout(3000);
}
