import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { Icon } from "../components/Icon";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";
import { useOmiStore } from "../store/useOmiStore";
import { errorMessage, toast } from "../store/useToastStore";

interface ConversationPageProps {
  onSendText?: (text: string) => Promise<void> | void;
}

export function ConversationPage({ onSendText }: ConversationPageProps) {
  const qc = useQueryClient();
  const messages = useOmiStore((s) => s.messages);
  const streamingText = useOmiStore((s) => s.streamingText);
  const setMessages = useOmiStore((s) => s.setMessages);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);
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
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setDraft("");
    try {
      if (onSendText) {
        await onSendText(text);
      } else {
        await apiClient.sendMessage(text);
        await qc.invalidateQueries({ queryKey: ["messages"] });
        const res = await query.refetch();
        if (res.data) setMessages(res.data);
      }
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  const handleSend = (e: FormEvent) => {
    e.preventDefault();
    void submit();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
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

      {messages.length > 0 && (
        <div className="chat">
          {messages.map((msg) => {
            const isUser = msg.role === "user";
            return (
              <article
                key={msg.id}
                className={`msg ${isUser ? "msg--user" : "msg--assistant"}`}
                data-testid={`msg-${msg.id}`}
              >
                <div className="msg__meta">
                  <span className="msg__author">{isUser ? "Vous" : "Jarvis"}</span>
                  <time dateTime={msg.created_at}>
                    {new Date(msg.created_at).toLocaleTimeString("fr-FR", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </div>

                <div className="msg__content">{msg.content}</div>

                {msg.tools && msg.tools.length > 0 && (
                  <div className="stack" style={{ width: "100%" }}>
                    {msg.tools.map((tool) => (
                      <details key={tool.id} className="tool">
                        <summary>
                          <Icon name="chevron" size={14} />
                          <span className="tool__name">{tool.name}</span>
                          <span className={`badge ${tool.ok ? "badge--ok" : "badge--danger"}`}>
                            risque {tool.risk} · {tool.ok ? "ok" : "erreur"}
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
                  <div className="msg__usage">
                    {msg.usage.model} · {msg.usage.tokens_in} → {msg.usage.tokens_out} jetons · $
                    {msg.usage.cost.toFixed(4)}
                  </div>
                )}
              </article>
            );
          })}

          {streamingText && (
            <article className="msg msg--assistant msg--streaming">
              <div className="msg__meta">
                <span className="msg__author">Jarvis</span>
                <span>en cours…</span>
              </div>
              <div className="msg__content">{streamingText}</div>
            </article>
          )}
          <div ref={endRef} />
        </div>
      )}

      <form className="composer chat-dock" onSubmit={handleSend} noValidate>
        <textarea
          ref={inputRef}
          rows={1}
          className="composer__input"
          placeholder="Écrire un message à Jarvis…"
          aria-label="Nouveau message"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
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
