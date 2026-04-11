/**
 * Shared E2E test helpers.
 *
 * Auth: HttpOnly JWT cookies + localStorage "nxentra_authenticated" flag.
 * Navigation after login must stay within the same browser origin to
 * preserve cookies.
 */

import { type Page, expect } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";
const BASE = process.env.E2E_BASE_URL || "http://localhost:3000";

/**
 * Login and navigate to a target page.
 * Uses full absolute URL for navigation to ensure same-origin cookie handling.
 */
export async function loginAndGo(page: Page, targetPath: string) {
  // Step 1: Login
  await page.goto(`${BASE}/login`);
  await page.waitForLoadState("networkidle");
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Step 2: Wait for redirect away from login
  await page.waitForURL((url) => !url.pathname.includes("/login"), {
    timeout: 30000,
  });
  await page.waitForTimeout(2000);

  // Step 3: Handle select-company if needed
  if (page.url().includes("/select-company")) {
    await page.waitForTimeout(1000);
    const card = page.locator("button, a").first();
    if (await card.isVisible({ timeout: 3000 }).catch(() => false)) {
      await card.click();
    }
    await page.waitForTimeout(3000);
  }

  // Step 4: Navigate to target using full URL (same origin = cookies preserved)
  const fullUrl = `${BASE}${targetPath}`;
  await page.goto(fullUrl);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(3000);

  // Step 5: If we got redirected back to login, the auth didn't stick
  // Log the current state for debugging
  if (page.url().includes("/login")) {
    const cookies = await page.context().cookies();
    const localStorage = await page.evaluate(() => {
      const items: Record<string, string> = {};
      for (let i = 0; i < window.localStorage.length; i++) {
        const key = window.localStorage.key(i);
        if (key) items[key] = window.localStorage.getItem(key) || "";
      }
      return items;
    });
    console.log("Auth debug - URL:", page.url());
    console.log("Auth debug - Cookies:", cookies.map(c => `${c.name}=${c.value.substring(0, 20)}...`));
    console.log("Auth debug - LocalStorage:", localStorage);
  }
}
