import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const production = !!process.env.CI || process.env.PLAYWRIGHT_PRODUCTION === "1";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  retries: 0, // A failed journey must be investigated, not hidden by retries.
  forbidOnly: !!process.env.CI,
  timeout: 45_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { open: "never" }], ["json", { outputFile: "test-results/results.json" }]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      name: "Replay API + worker",
      command: process.env.PYTHON ? '"' + process.env.PYTHON + '" -m scripts.e2e_server' : "python3 -m scripts.e2e_server",
      cwd: path.resolve(__dirname, "../../backend"),
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false, // Never run mutating tests against an existing workspace.
      timeout: 120_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
    {
      name: production ? "Next production server" : "Next dev server",
      command: production ? "node scripts/serve-e2e.mjs" : "npm run dev -- --hostname 127.0.0.1 --port 3000",
      url: "http://127.0.0.1:3000",
      reuseExistingServer: false,
      timeout: 120_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
      env: { API_INTERNAL_URL: "http://127.0.0.1:8000", NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});
