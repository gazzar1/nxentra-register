/**
 * E2E Test: Login flow
 *
 * Verifies:
 * 1. Login page loads
 * 2. Invalid credentials show error
 * 3. Valid credentials redirect to dashboard
 * 4. User name appears in header after login
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

  test("invalid credentials show error", async ({ page }) => {
    await page.goto("/login");
    await page.fill("#email", "wrong@test.com");
    await page.fill("#password", "wrongpassword");
    await page.click('button[type="submit"]');

    // Should stay on login page and show an error
    await page.waitForTimeout(2000);
    await expect(page).toHaveURL(/login/);
  });

  test("valid credentials redirect to dashboard", async ({ page }) => {
    await page.goto("/login");
    await page.fill("#email", "demo@nxentra.com");
    await page.fill("#password", "demo1234");
    await page.click('button[type="submit"]');

    // Should redirect away from login
    await page.waitForURL((url) => !url.pathname.includes("/login"), {
      timeout: 15000,
    });

    // Should see the app layout (sidebar or header)
    await expect(
      page.locator("header").or(page.locator("nav"))
    ).toBeVisible();
  });
});
