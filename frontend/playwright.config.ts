import { defineConfig } from "@playwright/test";
import path from "path";

const AUTH_FILE = path.join(__dirname, "e2e", ".auth", "user.json");

export default defineConfig({
  testDir: "./e2e",
  timeout: 60000,
  retries: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:3000",
    headless: true,
    screenshot: "only-on-failure",
    trace: "on-first-retry",
  },
  projects: [
    // Setup: login once and save auth state
    {
      name: "setup",
      testMatch: /auth\.setup\.ts/,
    },
    // All other tests reuse the saved auth state
    {
      name: "chromium",
      use: {
        browserName: "chromium",
        storageState: AUTH_FILE,
      },
      dependencies: ["setup"],
      testIgnore: /auth\.setup\.ts/,
    },
  ],
});
