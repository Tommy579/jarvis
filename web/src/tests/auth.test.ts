import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { HttpError } from "../api/client";
import {
  captureTokenFromUrl,
  clearAuthToken,
  getAuthToken,
  setAuthToken,
} from "../lib/authToken";

const KEY = "jarvis_api_token";

describe("jeton d'acces de l'interface", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    clearAuthToken();
  });

  it("capture le jeton depuis l'URL et nettoie l'adresse", () => {
    window.history.replaceState({}, "", "/?token=abc123&autre=1");

    const captured = captureTokenFromUrl();

    expect(captured).toBe("abc123");
    expect(getAuthToken()).toBe("abc123");
    expect(window.location.search).toBe("?autre=1");
  });

  it("ne fait rien quand l'URL ne contient pas de jeton", () => {
    window.history.replaceState({}, "", "/?autre=1");
    expect(captureTokenFromUrl()).toBeNull();
    expect(getAuthToken()).toBe("");
  });

  it("priorise sessionStorage sur localStorage", () => {
    window.localStorage.setItem(KEY, "ancien");
    window.sessionStorage.setItem(KEY, "recent");
    expect(getAuthToken()).toBe("recent");
  });

  it("migre un jeton localStorage existant vers sessionStorage sans le laisser en localStorage", () => {
    window.localStorage.setItem(KEY, "historique");
    expect(getAuthToken()).toBe("historique");
    setAuthToken("historique");
    expect(window.sessionStorage.getItem(KEY)).toBe("historique");
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("ignore un jeton vide et efface les deux stockages", () => {
    setAuthToken("   ");
    expect(getAuthToken()).toBe("");
    setAuthToken("valeur");
    clearAuthToken();
    expect(window.sessionStorage.getItem(KEY)).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("expose le statut HTTP dans HttpError", () => {
    const err = new HttpError("Jeton d'acces invalide ou manquant.", 401);
    expect(err).toBeInstanceOf(Error);
    expect(err.status).toBe(401);
    expect(err.name).toBe("HttpError");
  });
});
