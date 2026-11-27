import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { HttpError } from "../api/client";
import { describeApiError } from "../lib/apiErrors";
import { ErrorBanner } from "../components/ui";

describe("describeApiError", () => {
  it("traduit un echec reseau en message francais explicite", () => {
    const msg = describeApiError(
      new TypeError("Failed to fetch"),
      "Impossible de charger les objectifs autonomes."
    );
    expect(msg).toContain("Impossible de charger les objectifs autonomes.");
    expect(msg).toContain("injoignable");
  });

  it("distingue un refus d'autorisation d'une panne serveur", () => {
    expect(describeApiError(new HttpError("nope", 401), "Echec.")).toContain("Acces refuse");
    expect(describeApiError(new HttpError("nope", 403), "Echec.")).toContain("Acces refuse");
    expect(describeApiError(new HttpError("boom", 500), "Echec.")).toContain("erreur interne");
  });

  it("signale un service absent sur cette version de jarvis-core", () => {
    const msg = describeApiError(new HttpError("Not Found", 404), "Impossible de charger X.");
    expect(msg).toContain("Impossible de charger X.");
    expect(msg).toContain("pas disponible");
  });

  it("conserve le detail metier renvoye par l'API", () => {
    const msg = describeApiError(new HttpError("Budget depasse", 400), "Echec.");
    expect(msg).toBe("Echec. Budget depasse");
  });

  it("retombe sur le libelle fourni pour une erreur inconnue", () => {
    expect(describeApiError(undefined, "Impossible de charger Y.")).toBe(
      "Impossible de charger Y."
    );
    expect(describeApiError({ weird: true }, "Impossible de charger Y.")).toBe(
      "Impossible de charger Y."
    );
  });
});

describe("ErrorBanner", () => {
  it("affiche le message et un bouton Reessayer actionnable", () => {
    const html = renderToStaticMarkup(
      <ErrorBanner message="Impossible de charger les alertes." onRetry={() => undefined} />
    );
    expect(html).toContain('role="alert"');
    expect(html).toContain("Impossible de charger les alertes.");
    expect(html).toContain("Réessayer");
  });

  it("desactive le bouton pendant une nouvelle tentative", () => {
    const html = renderToStaticMarkup(
      <ErrorBanner message="Erreur." onRetry={() => undefined} retrying />
    );
    expect(html).toContain("Nouvelle tentative");
    expect(html).toContain("disabled");
  });

  it("n'affiche pas de bouton Reessayer sans handler", () => {
    const html = renderToStaticMarkup(<ErrorBanner message="Erreur." />);
    expect(html).not.toContain("Réessayer");
  });
});
