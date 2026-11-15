import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  workers: 1,
  timeout: 420_000,
  use: { browserName: 'chromium', baseURL: 'http://localhost:4200', trace: 'off' },
  reporter: [['line'], ['json', { outputFile: process.env['ARCHIVE_BROWSER_REPORT'] || '../var/browser/results.json' }]],
  outputDir: '../var/browser/artifacts',
});
