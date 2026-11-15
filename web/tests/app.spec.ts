import { expect, test } from "@playwright/test";

const MOCK_API = "http://127.0.0.1:8766";

test.describe("OMI 2.0 Web E2E", () => {
  test("transitions entre les 6 etats de la sphere vocale", async ({ page, request }) => {
    await page.goto("/");

    const statusBadge = page.getByTestId("ws-status-badge");
    const sphere = page.getByTestId("omi-sphere");

    await expect(statusBadge).toHaveText("Connecte");
    await expect(sphere).toHaveAttribute("data-sphere-state", "idle");

    const states = ["listening", "thinking", "speaking", "alert", "idle", "offline"] as const;
    for (const value of states) {
      const resp = await request.post(`${MOCK_API}/mock/emit`, {
        data: { type: "state", payload: { value } },
      });
      expect(resp.ok()).toBeTruthy();
      await expect(sphere).toHaveAttribute("data-sphere-state", value);
    }
  });

  test("approbation d'une action niveau 2 sur la page Alertes", async ({ page, request }) => {
    await page.goto("/");
    await expect(page.getByTestId("ws-status-badge")).toHaveText("Connecte");

    const approvalId = `appr_e2e_${Date.now()}`;
    const emitResp = await request.post(`${MOCK_API}/mock/emit`, {
      data: {
        type: "approval.request",
        payload: {
          id: approvalId,
          description: "Valider la commande protegee de test",
          risk: 2,
          expires_at: new Date(Date.now() + 120_000).toISOString(),
        },
      },
    });
    expect(emitResp.ok()).toBeTruthy();

    await page.locator('nav[aria-label="Navigation principale"] button', { hasText: "Alertes" }).click();
    const card = page.getByTestId(`approval-${approvalId}`);
    await expect(card).toBeVisible();
    await expect(card).toContainText("Risque 2 | pending");

    await card.getByRole("button", { name: "Accepter" }).click();
    await expect(card).toContainText("Risque 2 | approved");
    await expect(page.locator(".ok-banner")).toContainText(`Action ${approvalId} approuvee.`);
  });

  test("adaptation responsive mobile 375x812 sans debordement horizontal", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/");

    const homeOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );
    expect(homeOverflow).toBeTruthy();

    await page.locator('nav[aria-label="Navigation principale"] button', { hasText: "Memoire" }).click();
    await expect(page.getByRole("heading", { name: "Memoire et Onboarding" })).toBeVisible();
    const memoryOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );
    expect(memoryOverflow).toBeTruthy();

    await page.locator('nav[aria-label="Navigation principale"] button', { hasText: "Configuration" }).click();
    await expect(
      page.getByRole("heading", { name: "Modeles, Integrations, Regles et Capteurs" })
    ).toBeVisible();
    const configOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );
    expect(configOverflow).toBeTruthy();
  });

  test("fallback Canvas 2D actif lorsque WebGL est desactive (?fallback2d=1)", async ({ page }) => {
    await page.goto("/?fallback2d=1");

    const sphere = page.getByTestId("omi-sphere");
    await expect(sphere).toHaveAttribute("data-renderer", "canvas2d");

    const canvas2d = sphere.locator("canvas");
    await expect(canvas2d).toBeVisible();
    await expect(canvas2d).toHaveAttribute("aria-label", /Sphere OMI en mode Canvas 2D/);
  });
});
