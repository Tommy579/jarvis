import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { Icon } from "../components/Icon";
import { QueryErrorBanner } from "../components/QueryErrorBanner";
import { EmptyState, PageHeader, Segmented, Skeleton } from "../components/ui";
import { useJarvisStore } from "../store/useJarvisStore";
import { toast } from "../store/useToastStore";

interface AlertsApprovalsPageProps {
  onApproveAction: (id: string, approved: boolean) => Promise<void>;
  onAckAlert: (id: string, tooNoisy?: boolean) => Promise<void>;
}

const URGENCY_LABELS: Record<number, string> = {
  0: "Info",
  1: "Normal",
  2: "Important",
  3: "Urgent",
  4: "Critique",
};

const ALERT_STATUS_LABELS: Record<string, string> = {
  sent: "Envoyée",
  acked: "Acquittée",
  too_noisy: "Trop dérangeante",
  noisy: "Masquée",
  deferred: "Différée",
};

type AlertFilter = "all" | "urgent" | "unacked";

function formatRemaining(expiresAt: string, nowMs: number): { label: string; pct: number } {
  const diff = Math.max(0, Math.floor((new Date(expiresAt).getTime() - nowMs) / 1000));
  const min = Math.floor(diff / 60);
  const sec = diff % 60;
  const pct = Math.min(100, Math.max(0, (diff / 120) * 100));
  return { label: `${min}m ${String(sec).padStart(2, "0")}s`, pct };
}

export function AlertsApprovalsPage({
  onApproveAction,
  onAckAlert,
}: AlertsApprovalsPageProps) {
  const approvals = useJarvisStore((s) => s.approvals);
  const mergeApprovals = useJarvisStore((s) => s.mergeApprovals);
  const alerts = useJarvisStore((s) => s.alerts);
  const mergeAlerts = useJarvisStore((s) => s.mergeAlerts);

  const [nowMs, setNowMs] = useState(() => Date.now());
  const [feedback, setFeedback] = useState<string | null>(null);
  const [alertFilter, setAlertFilter] = useState<AlertFilter>("all");

  const filteredAlerts = alerts.filter((a) => {
    if (alertFilter === "urgent") return a.urgency >= 3;
    if (alertFilter === "unacked") return a.status === "sent";
    return true;
  });

  const approvalsQuery = useQuery({
    queryKey: ["approvals"],
    queryFn: apiClient.getApprovals,
    refetchInterval: 5000,
  });

  const alertsQuery = useQuery({
    queryKey: ["alerts"],
    queryFn: apiClient.getAlerts,
    refetchInterval: 5000,
  });

  useEffect(() => {
    if (approvalsQuery.data) mergeApprovals(approvalsQuery.data);
  }, [approvalsQuery.data, mergeApprovals]);

  useEffect(() => {
    if (alertsQuery.data) mergeAlerts(alertsQuery.data);
  }, [alertsQuery.data, mergeAlerts]);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const handleDecision = async (id: string, approved: boolean) => {
    await onApproveAction(id, approved);
    const msg = `Action ${id} ${approved ? "approuvée" : "refusée"}.`;
    setFeedback(msg);
    toast.ok(msg);
  };

  const handleAck = async (id: string, tooNoisy = false) => {
    await onAckAlert(id, tooNoisy);
    const msg = tooNoisy
      ? `Règle ajustée : alerte ${id} marquée trop dérangeante.`
      : `Alerte ${id} acquittée.`;
    setFeedback(msg);
    toast.ok(msg);
  };

  return (
    <section className="page" aria-label="Alertes et approbations">
      <PageHeader
        title="Alertes et Approbations"
        description="Actions sensibles en attente de validation et journal des alertes envoyées."
        actions={
          <button
            type="button"
            className="btn"
            onClick={() => {
              approvalsQuery.refetch();
              alertsQuery.refetch();
            }}
          >
            <Icon name="refresh" size={16} />
            Synchroniser la file
          </button>
        }
      />

      {feedback && (
        <div className="ok-banner sr-only" role="status">
          {feedback}
        </div>
      )}

      <div className="grid-2" style={{ alignItems: "start" }}>
        <div className="card card--flush">
          <div className="card__head">
            <h2 className="card__title">Approbations (niveaux 2 et 3)</h2>
            <span className="badge mono">
              {approvals.filter((a) => a.status === "pending").length} en attente
            </span>
          </div>

          <QueryErrorBanner
            query={approvalsQuery}
            fallback="Impossible de charger les approbations en attente."
            testId="approvals-error-banner"
          />
          {approvalsQuery.isLoading && approvals.length === 0 && <Skeleton lines={3} />}
          {approvals.length === 0 && !approvalsQuery.isLoading && !approvalsQuery.isError && (
            <EmptyState title="Aucune action en attente.">
              Les outils de niveau 2 et 3 demandent votre accord avant de s'exécuter.
            </EmptyState>
          )}

          {approvals.length > 0 && (
            <ul className="list">
              {approvals.map((item) => {
                const pending = item.status === "pending";
                const statusLabel =
                  item.status === "pending"
                    ? "En attente"
                    : item.status === "approved"
                      ? "Approuvée"
                      : item.status === "denied"
                        ? "Refusée"
                        : "Expirée";
                const args = item.args as Record<string, unknown> | undefined;
                const displayDesc =
                  item.tool_name === "mail_send" && args && (args.to || args.subject || args.body)
                    ? `Envoi d'un mail\nÀ : ${String(args.to ?? "")}\nObjet : ${String(args.subject ?? "")}\nMessage : ${String(args.body ?? "")}`
                    : item.description;
                const remaining = pending ? formatRemaining(item.expires_at, nowMs) : null;
                return (
                  <li
                    key={item.id}
                    data-testid={`approval-${item.id}`}
                    data-pending={pending}
                    data-risk={item.risk}
                    className="approval"
                  >
                    <div className="cluster" style={{ justifyContent: "space-between" }}>
                      <span
                        className={`badge ${
                          item.risk === 3
                            ? "badge--danger"
                            : item.status === "approved"
                              ? "badge--ok"
                              : "badge--accent"
                        }`}
                      >
                        {statusLabel}
                      </span>
                      {remaining && (
                        <span className="countdown">Expire dans {remaining.label}</span>
                      )}
                    </div>
                    {remaining && (
                      <div className="approval__timer" aria-hidden="true">
                        <div
                          className="approval__timer-fill"
                          style={{ width: `${remaining.pct}%` }}
                        />
                      </div>
                    )}
                    <div className="approval__desc" style={{ whiteSpace: "pre-line" }}>
                      {displayDesc}
                    </div>
                    {pending && (
                      <div className="cluster">
                        <button
                          type="button"
                          className="btn btn--ok btn--sm"
                          onClick={() => handleDecision(item.id, true)}
                        >
                          <Icon name="check" size={14} />
                          Accepter
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost btn--danger btn--sm"
                          onClick={() => handleDecision(item.id, false)}
                        >
                          Refuser
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="card card--flush">
          <div className="card__head">
            <h2 className="card__title">Journal des alertes</h2>
            <span className="badge mono">{filteredAlerts.length}</span>
          </div>

          {alerts.length > 0 && (
            <div className="card__toolbar">
              <Segmented<AlertFilter>
                label="Filtrer les alertes"
                value={alertFilter}
                onChange={setAlertFilter}
                options={[
                  { value: "all", label: `Toutes (${alerts.length})` },
                  { value: "urgent", label: "Urgentes" },
                  { value: "unacked", label: "Non acquittées" },
                ]}
              />
            </div>
          )}

          <QueryErrorBanner
            query={alertsQuery}
            fallback="Impossible de charger le journal des alertes."
            testId="alerts-error-banner"
          />
          {alertsQuery.isLoading && alerts.length === 0 && <Skeleton lines={3} />}
          {filteredAlerts.length === 0 && !alertsQuery.isLoading && !alertsQuery.isError && (
            <EmptyState title="Aucune alerte envoyée.">
              Les notifications urgentes filtrées par les règles d'attention apparaissent ici.
            </EmptyState>
          )}

          {filteredAlerts.length > 0 && (
            <ul className="list">
              {filteredAlerts.map((alert) => (
                <li key={alert.id} data-testid={`alert-${alert.id}`} className="row">
                  <div className="row__body">
                    <div className="cluster" style={{ justifyContent: "space-between" }}>
                      <span className={`badge ${alert.urgency >= 3 ? "badge--danger" : ""}`}>
                        {URGENCY_LABELS[alert.urgency] ?? `Urgence ${alert.urgency}`} ·{" "}
                        {ALERT_STATUS_LABELS[alert.status] ?? alert.status}
                      </span>
                      <span className="mono muted" style={{ fontSize: "0.75rem" }}>
                        {alert.channel}
                      </span>
                    </div>
                    <div>{alert.message}</div>
                    <div className="row__meta">Motif : {alert.reason}</div>
                    {alert.status === "sent" && (
                      <div className="cluster" style={{ marginTop: 4 }}>
                        <button
                          type="button"
                          className="btn btn--sm"
                          onClick={() => handleAck(alert.id, false)}
                        >
                          Acquitter
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => handleAck(alert.id, true)}
                        >
                          Trop dérangeant
                        </button>
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
