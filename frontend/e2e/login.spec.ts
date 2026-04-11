/**
 * E2E Test: Login flow
 */

import { test, expect } from "@playwright/test";

test.describe("Login", () => {
  test("login page loads correctly", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator("h2")).toContainText("Sign in");
    await expect(page.locator("#email")).toBeVisible();
    await expect(page.locator("#password")).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test("invalid credentials show error or stay on login", async ({ page }) => {
    await page.goto("/login");
    await page.fill("#email", "wrong@test.com");
    await page.fill("#password", "wrongpassword");
    await page.click('button[type="submit"]');
    await page.waitForTimeout(3000);
    // Should stay on login page
    expect(page.url()).toContain("/login");
  });

  test("valid credentials navigate away from login", async ({ page }) => {
    const email = process.env.E2E_EMAIL || "demo@nxentra.com";
    const password = process.env.E2E_PASSWORD || "demo1234";

    await page.goto("/login");
    await page.fill("#email", email);
    await page.fill("#password", password);
    await page.click('button[type="submit"]');

    // Wait for any post-login page (dashboard, select-company, verify, etc.)
    await page.waitForURL(
      (url) => !url.pathname.endsWith("/login"),
      { timeout: 30000 }
    );

    // We navigated somewhere — login worked
    expect(page.url()).not.toContain("/login");
  });
});
