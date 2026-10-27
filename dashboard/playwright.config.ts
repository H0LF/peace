import { defineConfig } from '@playwright/test'

// End-to-end tests against the real API (fresh seeded *_test database) serving the built dashboard.
// Run `npm run build` first. PEACE_CHROMIUM points at a local Chromium when the bundled one is absent.
export default defineConfig({
  testDir: 'e2e',
  workers: 1, // one shared database
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8431',
    locale: 'pt-PT',
    timezoneId: 'Europe/Lisbon',
    viewport: { width: 1440, height: 900 },
    launchOptions: process.env.PEACE_CHROMIUM ? { executablePath: process.env.PEACE_CHROMIUM } : {},
  },
  webServer: {
    command: 'uv run --project .. python ../scripts/e2e_server.py',
    url: 'http://127.0.0.1:8431/health',
    timeout: 60_000,
    reuseExistingServer: false,
  },
})
