import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { apiClient, HttpError } from "../api/client";
import { captureTokenFromUrl, getAuthToken, setAuthToken } from "../lib/authToken";

/**
 * Porte d'authentification de l'interface.
 *
 * Le serveur refuse toute route `/api/*` sans jeton des que `JARVIS_API_TOKEN` est defini.
 * Sans cette porte, l'interface affichait des erreurs 401 partout sans aucun moyen de
 * saisir le jeton : le seul chemin documente etait de l'ecrire a la main dans le
 * localStorage du navigateur.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<"checking" | "ok" | "locked">("checking");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const probe = useCallback(async (): Promise<boolean> => {
    try {
      await apiClient.getModels();
      setStatus("ok");
      setError(null);
      return true;
    } catch (err) {
      if (err instanceof HttpError && err.status === 401) {
        setStatus("locked");
        return false;
      }
      // Serveur injoignable ou autre panne : on laisse l'application afficher son etat.
      setStatus("ok");
      return true;
    }
  }, []);

  useEffect(() => {
    captureTokenFromUrl();
    void probe();
  }, [probe]);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const clean = value.trim();
    if (!clean) return;
    setSubmitting(true);
    setAuthToken(clean);
    const ok = await probe();
    setSubmitting(false);
    if (!ok) {
      setError("Jeton refuse par jarvis-core. Verifie JARVIS_API_TOKEN dans .env.");
      return;
    }
    setValue("");
  };

  if (status === "checking") {
    return (
      <div className="auth-gate" data-testid="auth-checking">
        <p className="muted">Connexion au service jarvis-core…</p>
      </div>
    );
  }

  if (status === "locked") {
    return (
      <div className="auth-gate">
        <form className="card stack auth-gate__card" onSubmit={onSubmit} data-testid="auth-gate">
          <div className="card__head">
            <h1 className="card__title">Jeton d&apos;acces requis</h1>
          </div>
          <p className="muted">
            Le service jarvis-core exige le jeton defini dans <span className="mono">.env</span>{" "}
            (<span className="mono">JARVIS_API_TOKEN</span>). Il est aussi fourni automatiquement
            quand tu lances JARVIS avec <span className="mono">make run</span>.
          </p>
          {error && (
            <p className="field__hint" role="alert">
              {error}
            </p>
          )}
          <label className="field">
            <span className="field__label">Jeton</span>
            <input
              className="input mono"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="colle ici la valeur de JARVIS_API_TOKEN"
              aria-label="Jeton d'acces jarvis-core"
            />
          </label>
          <div className="cluster">
            <button
              type="submit"
              className="btn btn--primary"
              disabled={submitting || value.trim().length === 0}
            >
              {submitting ? "Verification…" : "Se connecter"}
            </button>
            {getAuthToken() && (
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => {
                  setValue("");
                  setError(null);
                }}
              >
                Effacer
              </button>
            )}
          </div>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
