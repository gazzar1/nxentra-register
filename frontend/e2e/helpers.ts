/**
 * Shared E2E test helpers.
 */

import { type Page } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

export async function loginAndGo(page: Page, targetPath: string) {
  // Step 1: Login
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Step 2: Wait for redirect
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), {
    timeout: 30000,
  });
  await page.waitForTimeout(2000);

  // Step 3: Handle company selection
  if (page.url().includes("/select-company")) {
    await page.waitForTimeout(2000);

    // Click the first company card
    const card = page.locator("div.cursor-pointer").first();
    await card.click();

    // The company selection triggers a re-login API call then
    // window.location.href = "/dashboard". Wait for any URL change.
    try {
      await page.waitForURL((url) => !url.pathname.includes("/select-company"), {
        timeout: 15000,
      });
    } catch {
      // If URL didn't change, the re-login might have failed.
      // Try clicking again (page may not have been fully loaded)
      await page.waitForTimeout(2000);
    }
    await page.waitForTimeout(3000);
  }

  // Step 4: Navigate to target
  await page.goto(targetPath, { waitUntil: "networkidle" });
  await page.waitForTimeout(3000);
}
