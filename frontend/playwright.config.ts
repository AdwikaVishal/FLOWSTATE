import { defineConfig, devices } from '@playwright/test'
import os from 'node:os'
import path from 'node:path'

const frontendPort = 5199
const backendPort = 8010
const dataDir = path.join(os.tmpdir(), 'flowstate-console-e2e')
const frontendDir = process.cwd()
const backendDir = path.resolve(frontendDir, '../backend')

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: `rm -rf "${dataDir}" && ../.venv/bin/uvicorn flowstate.main:app --host 127.0.0.1 --port ${backendPort} --log-level warning`,
      cwd: backendDir,
      url: `http://127.0.0.1:${backendPort}/api/v1/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        FLOWSTATE_DATA_DIR: dataDir,
        FLOWSTATE_LOG_JSON: 'false',
        FLOWSTATE_AUTO_SEED_DEMO: 'true',
        FLOWSTATE_DEMO_APP_URL: `http://127.0.0.1:${backendPort}/demo-app/`,
      },
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${frontendPort} --strictPort`,
      cwd: frontendDir,
      url: `http://127.0.0.1:${frontendPort}`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        VITE_PROXY_TARGET: `http://127.0.0.1:${backendPort}`,
      },
    },
  ],
})