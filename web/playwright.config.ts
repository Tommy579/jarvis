import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  use: {
    headless: true,
  },
  projects: [
    {
      name: "mock",
      testMatch: /(app|section-errors)\.spec\.ts/,
      use: { baseURL: "http://127.0.0.1:4173" },
    },
    {
      name: "real-core",
      testMatch: /real-core\.spec\.ts/,
      use: { baseURL: "http://127.0.0.1:4174" },
    },
  ],
  webServer: [
    {
      command: "node mock-server.mjs --port 8766",
      url: "http://127.0.0.1:8766/api/messages",
      reuseExistingServer: !process.env.CI,
    },
    {
      command:
        "VITE_WS_URL=ws://127.0.0.1:8766/ws VITE_API_BASE=http://127.0.0.1:8766 npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
      url: "http://127.0.0.1:4173",
      reuseExistingServer: !process.env.CI,
    },
    {
      command:
        'bash -lc \'PY=../.venv/bin/python; [ -x "$PY" ] || PY=python; rm -rf /tmp/jarvis_e2e_pw && mkdir -p /tmp/jarvis_e2e_pw && PYTHONPATH=../packages/shared:../packages/core:../packages/desktop:.. JARVIS_ALLOW_INSECURE=1 JARVIS_API_TOKEN="" OPENROUTER_API_KEY="" GEMINI_API_KEY="" TELEGRAM_BOT_TOKEN="" TELEGRAM_ALLOWED_CHAT_ID="" JARVIS_DATA_DIR=/tmp/jarvis_e2e_pw $PY -m uvicorn jarvis_core.api.server:app --host 127.0.0.1 --port 8767\'',
      url: "http://127.0.0.1:8767/api/health",
      reuseExistingServer: !process.env.CI,
    },
    {
      command:
        "VITE_WS_URL=ws://127.0.0.1:8767/ws VITE_API_BASE=http://127.0.0.1:8767 npm run dev -- --host 127.0.0.1 --port 4174 --strictPort",
      url: "http://127.0.0.1:4174",
      reuseExistingServer: !process.env.CI,
    },
  ],
});
