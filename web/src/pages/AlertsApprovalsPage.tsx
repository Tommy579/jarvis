import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { Icon } from "../components/Icon";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";
import { useOmiStore } from "../store/useOmiStore";
import { toast } from "../store/useToastStore";

interface AlertsApprovalsPageProps {
  onApproveAction: (id: string, approved: boolean) => Promise<void>;
  onAckAlert: (id: string, tooNoisy?: boolean) => Promise<void>;
}

function formatRemaining(expiresAt: string, nowMs: number): string {
  const diff = Math.max(0, Math.floor((new Date(expiresAt).getTime() - nowMs) / 1000));
  const min = Math.floor(diff / 60);
  const sec = diff % 60;
  return `${min}m ${String(sec).padStart(2, "0")}s`;
}

export function AlertsApprovalsPage({
  onApproveAction,
  onAckAlert,
}: AlertsApprovalsPageProps) {
  const approvals = useOmiStore((s) => s.approvals);
  const setApprovals = useOmiStore((s) => s.setApprovals);
  const alerts = useOmiStore((s) => s.alerts);
  const setAlerts = useOmiStore((s) => s.setAlerts);

  const [nowMs, setNowMs] = useState(() => Date.now());
  const [feedback, setFeedback] = useState<string | null>(null);

  const approvalsQuery = useQuery({
    queryKey: ["approvals"],
    queryFn: apiClient.getApprovals,
  });

  const alertsQuery = useQuery({
    queryKey: ["alerts"],
    queryFn: apiClient.getAlerts,
  });

  useEffect(() => {
    if (approvalsQuery.data && approvals.length === 0) setApprovals(approvalsQuery.data);
  }, [approvalsQuery.data, approvals.length, setApprovals]);

  useEffect(() => {
    if (alertsQuery.data && alerts.length === 0) setAlerts(alertsQuery.data);
  }, [alertsQuery.data, alerts.length, setAlerts]);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const handleDecision = async (id: string, approved: boolean) => {
    await onApproveAction(id, approved);
    const msg = `Action ${id} ${approved ? "approuvee" : "refusee"}.`;
    setFeedback(msg);
    toast.ok(msg);
  };

  const handleAck = async (id: string, tooNoisy = false) => {
    await onAckAlert(id, tooNoisy);
    const msg = tooNoisy
      ? `Regle ajustee : alerte ${id} marquee trop derangeante.`
      : `Alerte ${id} acquittee.`;
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

          {approvalsQuery.isLoading && approvals.length === 0 && <Skeleton lines={3} />}
          {approvals.length === 0 && !approvalsQuery.isLoading && (
            <EmptyState title="Aucune action en attente.">
              Les outils de niveau 2 et 3 demandent votre accord avant de s'exécuter.
            </EmptyState>
          )}

          {approvals.length > 0 && (
            <ul className="list">
              {approvals.map((item) => {
                const pending = item.status === "pending";
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
                        Risque {item.risk} | {item.status}
                      </span>
                      {pending && (
                        <span className="countdown">
                          Expire dans {formatRemaining(item.expires_at, nowMs)}
                        </span>
                      )}
                    </div>
                    <div className="approval__desc">{item.description}</div>
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
            <span className="badge mono">{alerts.length}</span>
          </div>

          {alertsQuery.isLoading && alerts.length === 0 && <Skeleton lines={3} />}
          {alerts.length === 0 && !alertsQuery.isLoading && (
            <EmptyState title="Aucune alerte envoyée.">
              Les notifications urgentes filtrées par les règles d'attention apparaissent ici.
            </EmptyState>
          )}

          {alerts.length > 0 && (
            <ul className="list">
              {alerts.map((alert) => (
                <li key={alert.id} data-testid={`alert-${alert.id}`} className="row">
                  <div className="row__body">
                    <div className="cluster" style={{ justifyContent: "space-between" }}>
                      <span className={`badge ${alert.urgency >= 3 ? "badge--danger" : ""}`}>
                        Urgence {alert.urgency} | {alert.status}
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
                          Trop derangeant
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
