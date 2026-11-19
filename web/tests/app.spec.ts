import { expect, test } from "@playwright/test";

const MOCK_API = "http://127.0.0.1:8766";

test.describe("JARVIS 2.0 Web E2E", () => {
  test("transitions entre les 6 etats de la sphere vocale", async ({ page, request }) => {
    await page.goto("/");

    const statusBadge = page.getByTestId("ws-status-badge");
    const sphere = page.getByTestId("jarvis-sphere");

    await expect(statusBadge).toHaveText("Connecté");
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
    await expect(page.getByTestId("ws-status-badge")).toHaveText("Connecté");

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
    await expect(card).toContainText("En attente");

    await card.getByRole("button", { name: "Accepter" }).click();
    await expect(card).toContainText("Approuvée");
    await expect(page.locator(".ok-banner")).toContainText(`Action ${approvalId} approuvée.`);
  });

  test("adaptation responsive mobile 375x812 sans debordement horizontal", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/");

    const homeOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );
    expect(homeOverflow).toBeTruthy();

    await page.locator('nav[aria-label="Navigation principale"] button', { hasText: "Mémoire" }).click();
    await expect(page.getByRole("heading", { name: "Mémoire et Onboarding" })).toBeVisible();
    const memoryOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );
    expect(memoryOverflow).toBeTruthy();

    await page.locator('nav[aria-label="Navigation principale"] button', { hasText: "Configuration" }).click();
    await expect(
      page.getByRole("heading", { name: "Modèles, Intégrations, Règles et Capteurs" })
    ).toBeVisible();
    const configOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );
    expect(configOverflow).toBeTruthy();
  });

  test("fallback Canvas 2D actif lorsque WebGL est desactive (?fallback2d=1)", async ({ page }) => {
    await page.goto("/?fallback2d=1");

    const sphere = page.getByTestId("jarvis-sphere");
    await expect(sphere).toHaveAttribute("data-renderer", "canvas2d");

    const canvas2d = sphere.locator("canvas");
    await expect(canvas2d).toBeVisible();
    await expect(canvas2d).toHaveAttribute("aria-label", /Sphere JARVIS en mode Canvas 2D/);
  });

  test("navigation vers Objectifs autonomes et bascule Mode Objectif", async ({ page }) => {
    await page.goto("/");

    const modeBtn = page.getByRole("button", { name: "Mode Objectif" });
    await expect(modeBtn).toBeVisible();
    await modeBtn.click();
    await expect(modeBtn).toHaveAttribute("aria-pressed", "true");

    await page.locator('nav[aria-label="Navigation principale"] button', { hasText: "Objectifs" }).click();
    await expect(page.getByRole("heading", { name: "Objectifs autonomes" })).toBeVisible();
    await page.getByLabel("Nouvel objectif autonome").fill("Verifier le build complet et notifier");
    await page.getByRole("button", { name: "Lancer l'objectif" }).click();
    await expect(page.getByText("Verifier le build complet et notifier")).toBeVisible();
  });
});
