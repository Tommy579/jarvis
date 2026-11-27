import { toast } from "../store/useToastStore";

const DEDUPE_WINDOW_MS = 10_000;

let lastReport: { message: string; at: number } | null = null;

/** Affiche une erreur globale, en evitant de spammer le meme message en boucle. */
export function reportGlobalError(message: string, now: number = Date.now()): void {
  if (lastReport && lastReport.message === message && now - lastReport.at < DEDUPE_WINDOW_MS) {
    return;
  }
  lastReport = { message, at: now };
  toast.error(message);
}

/** Reinitialise la memoire de deduplication (tests, nouvelle session). */
export function resetGlobalErrorDedupe(): void {
  lastReport = null;
}

/**
 * Capture les erreurs qui echappent a React (promesses rejetees, erreurs de script) :
 * sans cela elles restaient invisibles dans la console du navigateur.
 */
export function installGlobalErrorHandlers(target: Window = window): () => void {
  const onError = (event: ErrorEvent): void => {
    console.error("[JARVIS] erreur non capturee", event.error ?? event.message);
    reportGlobalError(`Erreur inattendue : ${event.message || "inconnue"}`);
  };
  const onRejection = (event: PromiseRejectionEvent): void => {
    console.error("[JARVIS] promesse rejetee", event.reason);
    const reason = event.reason instanceof Error ? event.reason.message : String(event.reason);
    reportGlobalError(`Operation interrompue : ${reason}`);
  };

  target.addEventListener("error", onError);
  target.addEventListener("unhandledrejection", onRejection);
  return () => {
    target.removeEventListener("error", onError);
    target.removeEventListener("unhandledrejection", onRejection);
  };
}