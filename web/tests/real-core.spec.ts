import { expect, test } from "@playwright/test";

test.describe("JARVIS 2.0 Real Core E2E", () => {
  test("chat WebSocket reel, persistance SQLite et page Memoire", async ({ page }) => {
    await page.goto("/");

    const statusBadge = page.getByTestId("ws-status-badge");
    const sphere = page.getByTestId("jarvis-sphere");

    await expect(statusBadge).toHaveText("Connecté");
    await expect(sphere).toHaveAttribute("data-sphere-state", "idle");

    const promptInput = page.getByLabel("Message pour Jarvis");
    await promptInput.fill("Bonjour reel JARVIS");
    await page.getByRole("button", { name: "Envoyer" }).click();

    await expect(page.getByTestId("live-subtitles")).toContainText("Mode hors-ligne actif");
    await expect(sphere).toHaveAttribute("data-sphere-state", "idle");

    await page
      .locator('nav[aria-label="Navigation principale"] button', { hasText: "Mémoire" })
      .click();
    await expect(page.getByRole("heading", { name: "Mémoire et Onboarding" })).toBeVisible();

    await page
      .getByPlaceholder("Ex : Préfère les réponses concises le matin")
      .fill("Tom utilise Arch Linux");
    await page.getByRole("button", { name: "Ajouter" }).click();
    await expect(page.getByText("Tom utilise Arch Linux")).toBeVisible();

    await page.reload();
    await expect(page.getByTestId("ws-status-badge")).toHaveText("Connecté");
    await page
      .locator('nav[aria-label="Navigation principale"] button', { hasText: "Mémoire" })
      .click();
    await expect(page.getByText("Tom utilise Arch Linux")).toBeVisible();
  });
});
