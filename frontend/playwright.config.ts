import { defineConfig, devices } from "@playwright/test";

// Runs against a stack that is already up, because the real thing is what needs testing:
//   OPERATOR_TOKEN=... npx playwright test
// Set E2E_BASE_URL for another address, and E2E_ROUTING=1 where the road map is available.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  outputDir: "./test-results",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    // Your installed Chrome locally, so nothing extra is downloaded. CI installs Chromium.
    channel: process.env.CI ? undefined : "chrome",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"] } }],
});
