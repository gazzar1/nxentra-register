/**
 * Shared authentication setup for E2E tests.
 *
 * Logs in with demo credentials and saves the auth state
 * so subsequent tests can reuse the session.
 *
 * Prerequisites:
 * - Backend running at localhost:8000
 * - Frontend running at localhost:3000
 * - Demo company seeded (manage.py seed_demo_company)
 */

import { test as setup, expect } from "@playwright/test";
import path from "path";

const AUTH_FILE = path.join(__dirname, ".auth", "user.json");

setup("authenticate", async ({ page }) => {
  // Go to login page
  await page.goto("/login");

  // Fill credentials
  await page.fill("#email", "demo@nxentra.com");
  await page.fill("#password", "demo1234");

  // Submit
  await page.click('button[type="submit"]');

  // Wait for redirect to dashboard or company selector
  await page.waitForURL((url) => !url.pathname.includes("/login"), {
    timeout: 15000,
  });

  // Save auth state
  await page.context().storageState({ path: AUTH_FILE });
});

export { AUTH_FILE };
