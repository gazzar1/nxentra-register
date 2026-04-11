import { defineConfig } from "@playwright/test";

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
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
  // Don't start the dev server automatically — tests expect it to be running
  // along with the backend at localhost:8000.
  //
  // To run E2E tests:
  //   1. Start backend: cd backend && python manage.py runserver
  //   2. Start frontend: cd frontend && npm run dev
  //   3. Seed demo data: cd backend && python manage.py seed_demo_company
  //   4. Run tests: cd frontend && npm run test:e2e
});
