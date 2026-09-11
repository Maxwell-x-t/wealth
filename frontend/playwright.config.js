import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5184",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        "cd ../backend && WEALTH_DISABLE_SYNC=1 WEALTH_DEMO=1 WEALTH_DATA_DIR=/private/tmp/wealth-browser-e2e .venv/bin/python -m tests.seed_browser && WEALTH_DISABLE_SYNC=1 WEALTH_DEMO=1 WEALTH_DATA_DIR=/private/tmp/wealth-browser-e2e .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8014",
      url: "http://127.0.0.1:8014/api/health",
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command:
        "WEALTH_API_URL=http://127.0.0.1:8014 npm run dev -- --host 127.0.0.1 --port 5184 --strictPort",
      url: "http://127.0.0.1:5184",
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
