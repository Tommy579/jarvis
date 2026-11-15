interface NotFoundPageProps {
  onGoHome: () => void;
}

export function NotFoundPage({ onGoHome }: NotFoundPageProps) {
  return (
    <section className="card notfound" aria-label="Page introuvable">
      <div className="notfound__code">404</div>
      <h1 style={{ fontSize: "var(--fs-lg)" }}>Écran introuvable</h1>
      <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
        L'adresse demandée ne correspond à aucun écran d'OMI 2.0.
      </p>
      <button type="button" className="btn btn--primary" onClick={onGoHome}>
        Revenir à l'accueil
      </button>
    </section>
  );
}
