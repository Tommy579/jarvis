import { test } from "@playwright/test";

test("debug probe preflight", async ({ page }) => {
  const logs: string[] = [];

  page.on("request", (r) => {
    if (r.method() === "OPTIONS") {
      logs.push(`[preflight-req] ${r.url()} headers=${JSON.stringify(r.headers())}`);
    }
  });
  page.on("response", async (r) => {
    if (r.request().method() === "OPTIONS") {
      logs.push(
        `[preflight-res] ${r.status()} ${r.url()} acao=${r.headers()["access-control-allow-origin"]}`
      );
    }
  });
  page.on("requestfailed", (r) =>
    logs.push(`[requestfailed] ${r.method()} ${r.url()} :: ${r.failure()?.errorText}`)
  );

  await page.goto("/");
  await page.waitForTimeout(1000);

  await page.getByLabel("Message pour Jarvis").fill("Bonjour reel JARVIS");
  await page.getByRole("button", { name: "Envoyer" }).click();
  await page.waitForTimeout(400);

  await page
    .locator('nav[aria-label="Navigation principale"] button', { hasText: "Mémoire" })
    .click();
  await page.waitForTimeout(4000);

  console.log("=== PREFLIGHT LOGS ===");
  for (const l of logs) console.log(l);
  console.log("=== END ===");
});
