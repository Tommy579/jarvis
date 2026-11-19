import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { GoalTrackerCard } from "../components/GoalTrackerCard";
import { Icon } from "../components/Icon";
import { JarvisLogo } from "../components/JarvisLogo";
import { RichText } from "../components/RichText";
import { SlashCommandMenu } from "../components/SlashCommandMenu";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";
import { formatModelLabel } from "../lib/modelLabels";
import { filterSlashCommands, type SlashCommandDef } from "../lib/slashCommands";
import { useJarvisStore } from "../store/useJarvisStore";
import { errorMessage, toast } from "../store/useToastStore";

interface ConversationPageProps {
  onSendText?: (text: string) => Promise<void> | void;
  onOpenGoals?: () => void;
}

export function ConversationPage({ onSendText, onOpenGoals }: ConversationPageProps) {
  const qc = useQueryClient();
  const messages = useJarvisStore((s) => s.messages);
  const streamingText = useJarvisStore((s) => s.streamingText);
  const setMessages = useJarvisStore((s) => s.setMessages);
  const goalsMap = useJarvisStore((s) => s.goals);
  const goalMode = useJarvisStore((s) => s.goalMode);
  const setGoalMode = useJarvisStore((s) => s.setGoalMode);
  const [draft, setDraft] = useState("");
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const [sending, setSending] = useState(false);

  const activeGoal = Object.values(goalsMap)
    .filter((g) =>
      ["planning", "running", "verifying", "waiting_user", "waiting_approval", "paused_budget"].includes(
        g.status
      )
    )
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const query = useQuery({
    queryKey: ["messages"],
    queryFn: apiClient.getMessages,
  });

  useEffect(() => {
    if (query.data) {
      setMessages(query.data);
    }
  }, [query.data, setMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, streamingText]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  const slashItems = slashDismissed ? [] : filterSlashCommands(draft);
  const activeSlashIndex = slashItems.length > 0 ? Math.min(slashIndex, slashItems.length - 1) : 0;

  const sendDirect = async (text: string) => {
    if (!text || sending) return;
    setSending(true);
    setDraft("");
    setSlashIndex(0);
    setSlashDismissed(false);
    try {
      if (onSendText) {
        await onSendText(text);
      } else {
        await apiClient.sendMessage(text);
        await qc.invalidateQueries({ queryKey: ["messages"] });
        const res = await query.refetch();
        if (res.data) setMessages(res.data);
      }
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  const selectSlashItem = (item: SlashCommandDef, fromTab = false) => {
    if ((fromTab && item.type !== "immediate") || item.type === "goal") {
      setDraft(`${item.cmd} `);
      setSlashIndex(0);
      setSlashDismissed(false);
      inputRef.current?.focus();
      return;
    }
    void sendDirect(item.cmd);
  };

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    await sendDirect(text);
  };

  const handleSend = (e: FormEvent) => {
    e.preventDefault();
    void submit();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashItems.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIndex((activeSlashIndex + 1) % slashItems.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIndex((activeSlashIndex - 1 + slashItems.length) % slashItems.length);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        selectSlashItem(slashItems[activeSlashIndex], true);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashDismissed(true);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        selectSlashItem(slashItems[activeSlashIndex], false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    }
  };

  return (
    <section className="page page--narrow" aria-label="Historique de conversation">
      <PageHeader
        title="Conversation"
        description="Échanges complets avec Jarvis, appels d'outils tracés et consommation par modèle."
        actions={
          <button
            type="button"
            className="btn"
            onClick={async () => {
              const res = await query.refetch();
              if (res.data) setMessages(res.data);
            }}
          >
            <Icon name="refresh" size={16} />
            Actualiser
          </button>
        }
      />

      {query.isLoading && messages.length === 0 && (
        <div className="card">
          <Skeleton lines={4} />
        </div>
      )}

      {query.error && (
        <div className="banner" role="alert">
          <span>Impossible de charger l'historique : {String(query.error)}</span>
        </div>
      )}

      {messages.length === 0 && !query.isLoading && !streamingText && (
        <div className="card">
          <EmptyState title="Aucun échange enregistré pour le moment.">
            Écrivez un message ci-dessous pour démarrer une discussion avec Jarvis.
          </EmptyState>
        </div>
      )}

      {(messages.length > 0 || streamingText) && (
        <div className="chat">
          {messages.map((msg, idx) => {
            const isUser = msg.role === "user";
            const suiteRe = /\s*(?:\.\.\.|…)?\s*(?:\[suite\]|\(suite\)|suite\.\.\.)\s*$/i;
            const hasSuite = !isUser && suiteRe.test(msg.content);
            const cleanContent = isUser ? msg.content : msg.content.replace(suiteRe, "").trimEnd();
            return (
              <article
                key={msg.id}
                className={`msg ${isUser ? "msg--user" : "msg--assistant"}`}
                data-testid={`msg-${msg.id}`}
              >
                <div className="msg__meta">
                  {!isUser && <JarvisLogo size={16} className="msg__avatar" />}
                  <span className="msg__author">{isUser ? "Vous" : "Jarvis"}</span>
                  <time dateTime={msg.created_at}>
                    {new Date(msg.created_at).toLocaleTimeString("fr-FR", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                  {!isUser && cleanContent && (
                    <button
                      type="button"
                      className="msg__copy"
                      aria-label="Copier la réponse"
                      title="Copier"
                      onClick={() => {
                        void navigator.clipboard?.writeText(cleanContent);
                        toast.ok("Message copié.");
                      }}
                    >
                      Copier
                    </button>
                  )}
                </div>

                <div className="msg__content">
                  {isUser ? cleanContent : <RichText content={cleanContent} />}
                </div>

                {hasSuite && idx === messages.length - 1 && !sending && onSendText && (
                  <div>
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => {
                        onSendText("Donne la suite.");
                      }}
                    >
                      Suite
                    </button>
                  </div>
                )}

                {msg.tools && msg.tools.length > 0 && (
                  <div className="stack" style={{ width: "100%" }}>
                    {msg.tools.map((tool) => (
                      <details key={tool.id} className="tool">
                        <summary>
                          <Icon name="chevron" size={14} />
                          <span className="tool__name">{tool.name}</span>
                          <span className={`badge ${tool.ok ? "badge--ok" : "badge--danger"}`}>
                            niveau {tool.risk} · {tool.ok ? "réussi" : "erreur"}
                          </span>
                        </summary>
                        <div className="tool__body">
                          <div>{tool.summary}</div>
                          <pre>{JSON.stringify(tool.args, null, 2)}</pre>
                        </div>
                      </details>
                    ))}
                  </div>
                )}

                {msg.usage && (
                  <div className="msg__usage" title={msg.usage.model}>
                    {formatModelLabel(msg.usage.model)} · {msg.usage.tokens_in} → {msg.usage.tokens_out} jetons · $
                    {msg.usage.cost.toFixed(4)}
                  </div>
                )}
              </article>
            );
          })}

          {streamingText && (
            <article className="msg msg--assistant msg--streaming">
              <div className="msg__meta">
                <JarvisLogo size={16} className="msg__avatar" />
                <span className="msg__author">Jarvis</span>
                <span>en cours…</span>
              </div>
              <div className="msg__content">
                {streamingText.replace(/\s*(?:\.\.\.|…)?\s*(?:\[suite\]|\(suite\)|suite\.\.\.)\s*$/i, "").trimEnd()}
              </div>
            </article>
          )}
          <div ref={bottomRef} />
        </div>
      )}

      {activeGoal && (
        <div className="home-goal-banner">
          <GoalTrackerCard goal={activeGoal} compact onOpenGoals={onOpenGoals} />
        </div>
      )}

      <form className="composer chat-dock" onSubmit={handleSend} noValidate>
        <SlashCommandMenu
          items={slashItems}
          selectedIndex={activeSlashIndex}
          onHover={setSlashIndex}
          onSelect={(item) => selectSlashItem(item, false)}
        />
        <button
          type="button"
          className={`sensor-chip goal-mode-chip ${goalMode ? "goal-mode-chip--active" : ""}`}
          aria-pressed={goalMode}
          title="Activer le mode Objectif autonome (orchestration multi-agents jusqu'à vérification)"
          onClick={() => setGoalMode(!goalMode)}
        >
          <span className="dot" aria-hidden="true" />
          Objectif
        </button>
        <textarea
          ref={inputRef}
          rows={1}
          className="composer__input"
          placeholder={
            goalMode
              ? "Décris l'objectif autonome que Jarvis doit atteindre…"
              : "Écrire un message à Jarvis ou / pour les commandes…"
          }
          aria-label="Nouveau message"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setSlashIndex(0);
            setSlashDismissed(false);
          }}
          onKeyDown={handleKeyDown}
          disabled={sending}
        />
        <button
          type="submit"
          className="btn btn--primary send-btn"
          aria-label="Envoyer"
          disabled={sending || !draft.trim()}
        >
          <Icon name="send" size={17} />
        </button>
      </form>
    </section>
  );
}
