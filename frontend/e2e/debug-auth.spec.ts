/**
 * Debug test: traces exactly what happens during login and navigation.
 */

import { test, expect } from "@playwright/test";

const E2E_EMAIL = process.env.E2E_EMAIL || "demo@nxentra.com";
const E2E_PASSWORD = process.env.E2E_PASSWORD || "demo1234";

test("debug: trace login and navigation", async ({ page }) => {
  // Step 1: Login
  console.log("STEP 1: Going to /login");
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  console.log("  URL after goto:", page.url());

  // Step 2: Fill and submit
  console.log("STEP 2: Filling credentials and submitting");
  await page.fill("#email", E2E_EMAIL);
  await page.fill("#password", E2E_PASSWORD);
  await page.click('button[type="submit"]');

  // Step 3: Wait and check where we land
  await page.waitForTimeout(5000);
  console.log("STEP 3: URL after login submit + 5s wait:", page.url());

  // Step 4: Check cookies
  const cookies = await page.context().cookies();
  console.log("STEP 4: Cookies count:", cookies.length);
  for (const c of cookies) {
    console.log(`  Cookie: ${c.name} | domain=${c.domain} | path=${c.path} | secure=${c.secure} | httpOnly=${c.httpOnly} | sameSite=${c.sameSite}`);
  }

  // Step 5: Check localStorage
  const authFlag = await page.evaluate(() => localStorage.getItem("nxentra_authenticated"));
  console.log("STEP 5: localStorage nxentra_authenticated =", authFlag);

  // Step 6: Check page content
  const bodyText = await page.textContent("body");
  console.log("STEP 6: Body text (first 200 chars):", bodyText?.substring(0, 200));

  // Step 7: Try navigating to /dashboard
  console.log("STEP 7: Navigating to /dashboard");
  await page.goto("/dashboard");
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(3000);
  console.log("  URL after goto /dashboard:", page.url());

  // Step 8: Check cookies again
  const cookies2 = await page.context().cookies();
  console.log("STEP 8: Cookies count after navigation:", cookies2.length);
  for (const c of cookies2) {
    console.log(`  Cookie: ${c.name} | domain=${c.domain} | path=${c.path} | secure=${c.secure} | httpOnly=${c.httpOnly}`);
  }

  // Step 9: Check localStorage again
  const authFlag2 = await page.evaluate(() => localStorage.getItem("nxentra_authenticated"));
  console.log("STEP 9: localStorage after navigation =", authFlag2);

  // Step 10: Page content
  const bodyText2 = await page.textContent("body");
  console.log("STEP 10: Body after navigation (first 200 chars):", bodyText2?.substring(0, 200));

  // Always pass — this is a diagnostic test
  expect(true).toBe(true);
});
