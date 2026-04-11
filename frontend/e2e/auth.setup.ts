/**
 * Auth setup — runs once before all tests.
 *
 * Logs in and saves browser state (cookies + localStorage) so that
 * all subsequent tests start already authenticated.
 */

import { test as setup, expect } from "@playwright/test";
import path from "path";

const AUTH_FILE = path.join(__dirname, ".auth", "user.json");
const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

setup("authenticate", async ({ page }) => {
  await page.goto("/login");
  await page.waitForLoadState("networkidle");

  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Wait for redirect away from login
  await page.waitForURL((url) => !url.pathname.includes("/login"), {
    timeout: 30000,
  });

  // If on select-company, wait and pick the first one
  if (page.url().includes("/select-company")) {
    await page.waitForTimeout(2000);
    const card = page.locator("button, a, [role='button']").first();
    if (await card.isVisible({ timeout: 3000 }).catch(() => false)) {
      await card.click();
      await page.waitForTimeout(3000);
    }
  }

  // Wait for app to fully load
  await page.waitForTimeout(3000);

  // Save auth state (cookies + localStorage)
  await page.context().storageState({ path: AUTH_FILE });
});
