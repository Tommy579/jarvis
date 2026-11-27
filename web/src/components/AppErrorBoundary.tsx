import { Component, type ErrorInfo, type ReactNode } from "react";
import { toast } from "../store/useToastStore";

interface AppErrorBoundaryProps {
  children: ReactNode;
}

interface AppErrorBoundaryState {
  error: Error | null;
  componentStack: string;
}

/**
 * Filet de securite global de l'interface : sans lui, une exception pendant le rendu
 * laissait un ecran vide (seule la sphere 3D avait son propre boundary).
 */
export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null, componentStack: "" };

  static getDerivedStateFromError(error: Error): Partial<AppErrorBoundaryState> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Journal navigateur : visible dans la console et dans les rapports de crash du systeme.
    console.error("[JARVIS] rendu interrompu", error, info.componentStack);
    this.setState({ componentStack: info.componentStack ?? "" });
  }

  private readonly reload = (): void => {
    window.location.reload();
  };

  private readonly copyDetails = (details: string): void => {
    void navigator.clipboard
      .writeText(details)
      .then(() => toast.ok("Detail copie."))
      .catch(() => toast.error("Copie impossible : selectionne le texte affiche."));
  };

  render(): ReactNode {
    const { error, componentStack } = this.state;
    if (!error) return this.props.children;

    const details = [`${error.name}: ${error.message}`, componentStack].filter(Boolean).join("\n\n");
    return (
      <main className="page" role="alert" aria-live="assertive">
        <div className="card">
          <h1 style={{ fontSize: "var(--fs-xl)" }}>L'interface s'est interrompue</h1>
          <p>
            Une erreur d'affichage a arrete cette page. Tes donnees ne sont pas perdues : la
            memoire, les objectifs et les sauvegardes restent dans jarvis-core.
          </p>
          <div className="cluster" style={{ gap: 8 }}>
            <button type="button" className="btn btn--primary" onClick={this.reload}>
              Recharger l'interface
            </button>
            <button type="button" className="btn btn--ghost" onClick={() => this.copyDetails(details)}>
              Copier le detail
            </button>
          </div>
          <pre
            aria-label="Detail technique de l'erreur"
            style={{
              marginTop: 16,
              maxHeight: 240,
              overflow: "auto",
              whiteSpace: "pre-wrap",
              fontSize: "var(--fs-xs, 12px)",
              opacity: 0.8,
            }}
          >
            {details}
          </pre>
        </div>
      </main>
    );
  }
}