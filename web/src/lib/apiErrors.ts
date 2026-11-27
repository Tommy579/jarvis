import { HttpError } from "../api/client";

/**
 * Traduit une erreur de fetch/HTTP en message court et lisible en francais.
 *
 * `fallback` decrit l'action qui a echoue (ex: "Impossible de charger les objectifs.")
 * et sert de base quand l'erreur d'origine n'est pas exploitable telle quelle.
 */
export function describeApiError(err: unknown, fallback: string): string {
  if (err instanceof HttpError) {
    if (err.status === 401 || err.status === 403) {
      return "Acces refuse : jeton d'acces invalide ou expire.";
    }
    if (err.status === 404) {
      return `${fallback} Ce service n'est pas disponible sur cette version de jarvis-core.`;
    }
    if (err.status >= 500) {
      return `${fallback} Le service jarvis-core a renvoye une erreur interne (${err.status}).`;
    }
    return `${fallback} ${err.message}`;
  }
  // Un echec reseau (service arrete, proxy injoignable) remonte en TypeError.
  if (err instanceof TypeError) {
    return `${fallback} Le service jarvis-core est injoignable (reseau).`;
  }
  if (err instanceof Error && err.message) {
    return `${fallback} ${err.message}`;
  }
  return fallback;
}
