/**
 * Shared E2E test helpers.
 */

import { type Page } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

/**
 * Login then navigate to target path.
 */
export async function loginAndGo(page: Page, targetPath: string) {
  // Login
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Wait for login redirect
  await page.waitForURL((url) => !url.pathname.includes("/login"), {
    timeout: 30000,
  });
  await page.waitForTimeout(2000);

  // Handle company selection
  if (page.url().includes("/select-company")) {
    const card = page.locator("button, a").first();
    if (await card.isVisible({ timeout: 3000 }).catch(() => false)) {
      await card.click();
      await page.waitForTimeout(3000);
    }
  }

  // Ensure auth flag is in localStorage
  await page.evaluate(() => {
    if (localStorage.getItem("nxentra_authenticated") !== "true") {
      localStorage.setItem("nxentra_authenticated", "true");
    }
  });

  // Navigate to target
  await page.goto(targetPath, { waitUntil: "networkidle" });
  await page.waitForTimeout(3000);
}
