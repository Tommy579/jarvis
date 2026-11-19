/**
 * Jeton d'acces a l'API jarvis-core.
 *
 * Trois sources, dans cet ordre :
 *  1. `?token=...` dans l'URL : le lanceur officiel ouvre le navigateur avec le jeton,
 *     puis on nettoie l'URL pour ne pas le laisser dans l'historique ;
 *  2. sessionStorage : dure le temps de l'onglet (pas de jeton persistant en clair) ;
 *  3. localStorage : compatibilite avec les installations existantes.
 */

const STORAGE_KEY = "jarvis_api_token";

function hasWindow(): boolean {
  return typeof window !== "undefined";
}

function readSession(): string {
  if (!hasWindow()) return "";
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function readLocal(): string {
  if (!hasWindow()) return "";
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function getAuthToken(): string {
  return readSession() || readLocal();
}

export function setAuthToken(token: string): void {
  if (!hasWindow()) return;
  const clean = token.trim();
  if (!clean) return;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, clean);
  } catch {
    // sessionStorage indisponible (mode prive strict) : on garde localStorage.
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, clean);
  } catch {
    // ignore
  }
}

export function clearAuthToken(): void {
  if (!hasWindow()) return;
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

/**
 * Lit `?token=` dans l'URL, le memorise et retire le parametre sans recharger la page.
 * Retourne le jeton capture, ou null si l'URL n'en contient pas.
 */
export function captureTokenFromUrl(): string | null {
  if (!hasWindow()) return null;
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  if (!token) return null;
  setAuthToken(token);
  params.delete("token");
  const query = params.toString();
  const next = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  try {
    window.history.replaceState({}, "", next);
  } catch {
    // ignore
  }
  return token;
}
