import { defineConfig } from "@playwright/test";

// Set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to use a system Chromium when
// `npx playwright install chromium` is unavailable (e.g. restricted network).
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:4173",
    launchOptions: { executablePath, args: ["--no-sandbox"] },
  },
  webServer: [
    {
      // Real FastAPI app + FAKE orchestrator (no AI pipeline).
      command: "python3 ../tests/e2e_support/fake_server.py",
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: "npm run build && npm run preview -- --port 4173 --strictPort",
      url: "http://localhost:4173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
