import { defineConfig } from "@playwright/test";

// Smoke journeys: the real stack (make up + services + seed), driven by a real browser.
export default defineConfig({
  testDir: ".",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: 1,
  workers: 1,
  use: {
    baseURL: process.env.SMOKE_BASE_URL ?? "http://localhost:5173",
    trace: "on",
    screenshot: "on",
  },
  outputDir: process.env.SMOKE_ARTIFACTS ?? "test-results",
});
