import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useToastStore } from "../store/useToastStore";
import { Icon } from "./Icon";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {children && <div className="empty__body">{children}</div>}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" role="status" aria-label="Chargement">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} style={{ width: `${90 - i * 18}%` }} />
      ))}
    </div>
  );
}

/**
 * Bandeau d'erreur discret et reutilisable : message en francais, bouton
 * « Réessayer » qui relance le fetch concerne et bouton de fermeture.
 */
export function ErrorBanner({
  message,
  onRetry,
  retrying = false,
  onDismiss,
  testId,
}: {
  message: string;
  onRetry?: () => void | Promise<unknown>;
  retrying?: boolean;
  onDismiss?: () => void;
  testId?: string;
}) {
  return (
    <div className="banner banner--error" role="alert" data-testid={testId}>
      <span>{message}</span>
      {onRetry && (
        <button
          type="button"
          className="btn btn--sm banner__retry"
          onClick={() => void onRetry()}
          disabled={retrying}
        >
          <Icon name="refresh" size={14} />
          {retrying ? "Nouvelle tentative…" : "Réessayer"}
        </button>
      )}
      {onDismiss && (
        <button
          type="button"
          className="icon-btn icon-btn--sm"
          aria-label="Masquer l'erreur"
          onClick={onDismiss}
        >
          <Icon name="x" size={14} />
        </button>
      )}
    </div>
  );
}

/** Bouton destructif en deux temps : un premier clic arme, le second confirme. */
export function ConfirmButton({
  onConfirm,
  label = "Supprimer",
  confirmLabel = "Confirmer",
  className = "btn btn--ghost btn--danger btn--sm",
}: {
  onConfirm: () => void | Promise<void>;
  label?: string;
  confirmLabel?: string;
  className?: string;
}) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <button
      type="button"
      className={`${className}${armed ? " is-armed" : ""}`}
      onClick={() => {
        if (!armed) {
          setArmed(true);
          timer.current = window.setTimeout(() => setArmed(false), 3000);
          return;
        }
        window.clearTimeout(timer.current);
        setArmed(false);
        void onConfirm();
      }}
    >
      {armed ? confirmLabel : label}
    </button>
  );
}

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  return (
    <div className="toaster" role="status" aria-live="polite">
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.12 } }}
            transition={{ type: "spring", stiffness: 420, damping: 32 }}
            className={`toast toast--${t.tone}`}
          >
            <span className="toast__dot" aria-hidden="true" />
            <span className="toast__msg">{t.message}</span>
            <button
              type="button"
              className="icon-btn icon-btn--sm"
              aria-label="Fermer la notification"
              onClick={() => dismiss(t.id)}
            >
              <Icon name="x" size={14} />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: ReactNode }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          className="segmented__item"
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
