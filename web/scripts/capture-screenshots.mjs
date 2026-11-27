/**
 * Capture les ecrans de l'interface (mock server + Vite) dans docs/screenshots/.
 * Usage : node scripts/capture-screenshots.mjs
 */
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const WEB_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const ROOT_DIR = path.dirname(WEB_DIR);
const OUT_DIR = path.join(ROOT_DIR, "docs", "screenshots");
const MOCK_PORT = 8766;
const DEV_PORT = 4173;
const BASE_URL = `http://127.0.0.1:${DEV_PORT}`;
const VITE_BIN = path.join(WEB_DIR, "node_modules", ".bin", "vite");

const children = [];

function start(command, args, options) {
  const child = spawn(command, args, {
    stdio: "inherit",
    detached: true,
    ...options,
  });
  children.push(child);
  return child;
}

function cleanup() {
  for (const child of children) {
    if (child.exitCode !== null) continue;
    try {
      // detached : kill le groupe pour emporter les processus enfants (vite via node).
      process.kill(-child.pid, "SIGTERM");
    } catch {
      try {
        child.kill("SIGTERM");
      } catch {
        // deja termine
      }
    }
  }
}

process.on("exit", cleanup);
process.on("SIGINT", () => {
  cleanup();
  process.exit(130);
});

async function portIsFree(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    socket.once("connect", () => {
      socket.destroy();
      resolve(false);
    });
    socket.once("error", () => resolve(true));
    socket.setTimeout(1000, () => {
      socket.destroy();
      resolve(true);
    });
  });
}

async function waitFor(url, timeoutMs = 60_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // pas encore pret
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error(`Delai depasse en attendant ${url}`);
}

for (const port of [MOCK_PORT, DEV_PORT]) {
  if (!(await portIsFree(port))) {
    console.error(
      `Le port ${port} est deja utilise. Arrete le processus (mock ou vite) puis relance.`
    );
    process.exit(1);
  }
}

await mkdir(OUT_DIR, { recursive: true });

start(process.execPath, ["mock-server.mjs", "--port", String(MOCK_PORT)], { cwd: WEB_DIR });
start(VITE_BIN, ["--host", "127.0.0.1", "--port", String(DEV_PORT), "--strictPort"], {
  cwd: WEB_DIR,
  env: {
    ...process.env,
    VITE_API_BASE: `http://127.0.0.1:${MOCK_PORT}`,
    VITE_WS_URL: `ws://127.0.0.1:${MOCK_PORT}/ws`,
  },
});

await waitFor(`http://127.0.0.1:${MOCK_PORT}/api/messages`);
await waitFor(BASE_URL);

const browser = await chromium.launch();
try {
  const desktop = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: "dark",
    deviceScaleFactor: 1,
  });
  const page = await desktop.newPage();
  page.on("pageerror", (error) => console.error("[pageerror]", error.message));

  const shots = [
    ["accueil.png", "/"],
    ["conversation.png", "/conversation"],
    ["objectifs.png", "/goals"],
    ["memoire.png", "/memory"],
    ["configuration.png", "/config"],
  ];

  for (const [file, route] of shots) {
    await page.goto(`${BASE_URL}${route}`, { waitUntil: "load" });
    await page.waitForSelector(".app", { timeout: 15_000 });
    await page.waitForTimeout(1_500);
    await page.screenshot({ path: path.join(OUT_DIR, file), animations: "disabled" });
    console.log(`capture : ${file}`);
  }

  const mobile = await browser.newContext({
    viewport: { width: 414, height: 820 },
    colorScheme: "dark",
    deviceScaleFactor: 1,
    isMobile: true,
    hasTouch: true,
  });
  const mobilePage = await mobile.newPage();
  await mobilePage.goto(`${BASE_URL}/`, { waitUntil: "load" });
  await mobilePage.waitForSelector(".app", { timeout: 15_000 });
  await mobilePage.waitForTimeout(1_500);
  await mobilePage.screenshot({ path: path.join(OUT_DIR, "mobile.png"), animations: "disabled" });
  console.log("capture : mobile.png");
} finally {
  await browser.close();
  cleanup();
}