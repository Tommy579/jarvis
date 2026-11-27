import { expect, test } from "@playwright/test";

/**
 * Verifie le systeme d'erreur par section : une panne reseau sur un endpoint
 * n'efface plus les autres sections, affiche un bandeau francais avec
 * « Réessayer », et le bouton relance bien le fetch concerne.
 */
test.describe("Erreurs API par section", () => {
  test("bandeau d'erreur + Réessayer sur la page Objectifs", async ({ page }) => {
    let goalsCalls = 0;
    let failGoals = true;

    await page.route("**/api/goals**", async (route) => {
      goalsCalls += 1;
      if (failGoals) {
        await route.abort("failed");
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    });

    await page.goto("/goals");

    const banner = page.getByTestId("goals-error-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Impossible de charger les objectifs autonomes.");
    await expect(banner).toContainText("injoignable");

    // La liste vide trompeuse ne doit pas s'afficher pendant l'erreur.
    await expect(page.getByText("Aucun objectif en cours")).toHaveCount(0);

    const callsBefore = goalsCalls;
    failGoals = false;
    await banner.getByRole("button", { name: "Réessayer" }).click();

    await expect(banner).toHaveCount(0);
    await expect(page.getByText("Aucun objectif en cours")).toBeVisible();
    expect(goalsCalls).toBeGreaterThan(callsBefore);
  });

  test("une section en panne n'empeche pas les autres de s'afficher", async ({ page }) => {
    await page.route("**/api/alerts", (route) => route.abort("failed"));

    await page.goto("/alerts");

    // Le journal des alertes signale la panne...
    const alertsBanner = page.getByTestId("alerts-error-banner");
    await expect(alertsBanner).toBeVisible();
    await expect(alertsBanner).toContainText("Impossible de charger le journal des alertes.");
    await expect(page.getByText("Aucune alerte envoyée.")).toHaveCount(0);

    // ...mais les approbations restent chargees normalement.
    await expect(page.getByTestId("approvals-error-banner")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Approbations (niveaux 2 et 3)" })).toBeVisible();
  });

  test("le bandeau de section n'apparait pas sur une page non concernee", async ({ page }) => {
    await page.route("**/api/goals**", (route) => route.abort("failed"));

    await page.goto("/config");
    await expect(
      page.getByRole("heading", { name: "Modèles, Intégrations, Règles et Capteurs" })
    ).toBeVisible();

    await expect(page.getByTestId("section-error-goals")).toHaveCount(0);
    await expect(page.getByTestId("goals-error-banner")).toHaveCount(0);
  });

  test("aucun bandeau en double quand une page gere sa propre requete", async ({ page }) => {
    await page.route("**/api/goals**", (route) => route.abort("failed"));
    await page.goto("/goals");
    await page.waitForTimeout(1500);

    // Un seul bandeau pour les objectifs : celui de la page, pas celui de App.
    await expect(page.getByTestId("goals-error-banner")).toHaveCount(1);
    await expect(page.getByTestId("section-error-goals")).toHaveCount(0);
  });
});
