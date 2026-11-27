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

    // Le Mode Objectif n'est plus un bouton direct : il vit dans le widget
    // popover `home-goal-widget` (bouton « Développer » -> dialog « Mode Objectif »).
    const goalWidget = page.getByTestId("home-goal-widget");
    await expect(goalWidget).toBeVisible();
    await expect(goalWidget).toHaveAttribute("data-active", "false");

    await goalWidget.getByRole("button", { name: "Développer" }).click();

    const goalDialog = page.getByRole("dialog", { name: "Mode Objectif" });
    await expect(goalDialog).toBeVisible();
    await expect(goalDialog).toContainText("Mode Objectif autonome");

    const goalToggle = goalDialog.locator("button.goal-popover__toggle");
    await expect(goalToggle).toHaveAttribute("aria-pressed", "false");
    await expect(goalToggle).toHaveText("Désactivé");

    await goalToggle.click();
    await expect(goalToggle).toHaveAttribute("aria-pressed", "true");
    await expect(goalToggle).toHaveText("Activé");

    // Le widget reflete l'activation du mode objectif.
    await expect(goalWidget).toHaveAttribute("data-active", "true");
    await expect(goalWidget).toContainText("Objectif · Actif");

    await page.locator('nav[aria-label="Navigation principale"] button', { hasText: "Objectifs" }).click();
    await expect(page.getByRole("heading", { name: "Objectifs autonomes" })).toBeVisible();
    await page.getByLabel("Nouvel objectif autonome").fill("Verifier le build complet et notifier");
    await page.getByRole("button", { name: "Lancer l'objectif" }).click();
    await expect(page.getByText("Verifier le build complet et notifier")).toBeVisible();
  });
});

/**
 * Erreurs HTTP 500 des API cles.
 *
 * Contrairement aux tests de `section-errors.spec.ts` (qui simulent une panne
 * reseau via `route.abort`), ces tests verifient le chemin d'erreur applicatif :
 * le serveur repond bien, mais avec un statut 500. L'interface doit alors
 * afficher le bandeau francais dedie (« erreur interne (500) ») et proposer un
 * bouton « Réessayer » qui relance reellement la requete.
 *
 * Note : les requetes sont cross-origin (vite 4173 -> mock 8766), donc les
 * reponses forgees doivent porter les en-tetes CORS, sinon le navigateur
 * transformerait le 500 en TypeError reseau et le message teste ne serait
 * jamais celui du chemin 500.
 */
const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type,Authorization",
};

function fulfill500(route: import("@playwright/test").Route, detail: string) {
  return route.fulfill({
    status: 500,
    contentType: "application/json",
    headers: CORS_HEADERS,
    body: JSON.stringify({ detail }),
  });
}

test.describe("Erreurs HTTP 500 des API", () => {
  test("500 sur /api/goals : bandeau d'erreur + Réessayer qui relance la requete", async ({
    page,
  }) => {
    let goalsCalls = 0;
    let failGoals = true;

    // Predicat sur le pathname exact : /api/goals/models (liste des modeles)
    // doit continuer a fonctionner pour isoler la panne sur les objectifs.
    await page.route(
      (url) => url.pathname === "/api/goals",
      async (route) => {
        goalsCalls += 1;
        if (failGoals) {
          await fulfill500(route, "Internal Server Error");
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: CORS_HEADERS,
          body: JSON.stringify([]),
        });
      }
    );

    await page.goto("/goals");

    const banner = page.getByTestId("goals-error-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toHaveAttribute("role", "alert");
    await expect(banner).toContainText("Impossible de charger les objectifs autonomes.");
    await expect(banner).toContainText("erreur interne (500)");

    // Le bouton de relance doit etre present et actif.
    const retryBtn = banner.getByRole("button", { name: "Réessayer" });
    await expect(retryBtn).toBeVisible();
    await expect(retryBtn).toBeEnabled();

    // Aucune liste vide trompeuse pendant l'erreur.
    await expect(page.getByText("Aucun objectif en cours")).toHaveCount(0);

    // La liste des modeles (autre endpoint) n'est pas impactee.
    await expect(page.getByTestId("goal-models-error-banner")).toHaveCount(0);

    const callsBefore = goalsCalls;
    failGoals = false;
    await retryBtn.click();

    // Le bandeau disparait et l'etat vide legitime s'affiche.
    await expect(banner).toHaveCount(0);
    await expect(page.getByText("Aucun objectif en cours")).toBeVisible();
    expect(goalsCalls).toBeGreaterThan(callsBefore);
  });

  test("500 sur /api/alerts : bandeau + Réessayer, les approbations restent intactes", async ({
    page,
  }) => {
    let alertsCalls = 0;
    let failAlerts = true;

    await page.route(
      (url) => url.pathname === "/api/alerts",
      async (route) => {
        alertsCalls += 1;
        if (failAlerts) {
          await fulfill500(route, "Internal Server Error");
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: CORS_HEADERS,
          body: JSON.stringify([]),
        });
      }
    );

    await page.goto("/alerts");

    const banner = page.getByTestId("alerts-error-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Impossible de charger le journal des alertes.");
    await expect(banner).toContainText("erreur interne (500)");
    await expect(banner.getByRole("button", { name: "Réessayer" })).toBeVisible();

    // Pas de faux « aucune alerte » et pas de bandeau en double.
    await expect(page.getByText("Aucune alerte envoyée.")).toHaveCount(0);
    await expect(page.getByTestId("section-error-alerts")).toHaveCount(0);

    // La section voisine (approbations) n'est pas contaminee par le 500.
    await expect(page.getByTestId("approvals-error-banner")).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Approbations (niveaux 2 et 3)" })
    ).toBeVisible();

    const callsBefore = alertsCalls;
    failAlerts = false;
    await banner.getByRole("button", { name: "Réessayer" }).click();

    await expect(banner).toHaveCount(0);
    await expect(page.getByText("Aucune alerte envoyée.")).toBeVisible();
    expect(alertsCalls).toBeGreaterThan(callsBefore);
  });

  test("500 sur /api/models : bandeau + Réessayer sur l'onglet Modèles de la Configuration", async ({
    page,
  }) => {
    let modelsCalls = 0;
    let failModels = true;

    await page.route(
      (url) => url.pathname === "/api/models",
      async (route) => {
        modelsCalls += 1;
        if (failModels) {
          await fulfill500(route, "Internal Server Error");
          return;
        }
        await route.continue();
      }
    );

    await page.goto("/config#modeles");

    await expect(
      page.getByRole("heading", { name: "Modèles, Intégrations, Règles et Capteurs" })
    ).toBeVisible();

    const banner = page
      .locator(".banner--error")
      .filter({ hasText: "Impossible de charger la configuration des modèles." });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("erreur interne (500)");
    await expect(banner.getByRole("button", { name: "Réessayer" })).toBeVisible();

    // Aucun bandeau de section en doublon sur cette page.
    await expect(page.getByTestId("section-error-models")).toHaveCount(0);

    const callsBefore = modelsCalls;
    failModels = false;
    await banner.getByRole("button", { name: "Réessayer" }).click();

    await expect(banner).toHaveCount(0);
    await expect(page.getByLabel("Modèle pour le rôle agent")).toBeVisible();
    expect(modelsCalls).toBeGreaterThan(callsBefore);
  });
});
