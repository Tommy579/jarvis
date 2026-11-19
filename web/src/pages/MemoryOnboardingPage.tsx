import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { Icon } from "../components/Icon";
import { ConfirmButton, EmptyState, PageHeader, Segmented, Skeleton } from "../components/ui";
import { errorMessage, toast } from "../store/useToastStore";
import type { SensitivityLevel } from "../types/generated";

type TabId = "facts" | "wiki" | "people" | "review" | "onboarding";

const CATEGORY_LABELS: Record<string, string> = {
  general: "Général",
  identite: "Identité",
  travail: "Travail",
  rythme: "Rythme",
  gouts: "Goûts",
  sante: "Santé",
  finances: "Finances",
};

export function MemoryOnboardingPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabId>("facts");
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [sensitivityFilter, setSensitivityFilter] = useState("");
  const [newFactText, setNewFactText] = useState("");
  const [newFactCat, setNewFactCat] = useState("general");
  const [newFactSens, setNewFactSens] = useState<SensitivityLevel>("normal");
  const [editingFactId, setEditingFactId] = useState<string | null>(null);
  const [editingFactText, setEditingFactText] = useState("");
  const [selectedSlug, setSelectedSlug] = useState("moi");
  const [wikiDraft, setWikiDraft] = useState<string | null>(null);
  const [wikiPreview, setWikiPreview] = useState(false);
  const [onboardingDrafts, setOnboardingDrafts] = useState<Record<string, string>>({});
  const [personName, setPersonName] = useState("");
  const [personRelation, setPersonRelation] = useState("ami");
  const [personBirthday, setPersonBirthday] = useState("");
  const [personContact, setPersonContact] = useState("");
  const [personNotes, setPersonNotes] = useState("");

  const factsQuery = useQuery({
    queryKey: ["facts", search, categoryFilter, sensitivityFilter],
    queryFn: () =>
      apiClient.getFacts({
        q: search || undefined,
        category: categoryFilter || undefined,
        sensitivity: sensitivityFilter || undefined,
      }),
  });

  const reviewQuery = useQuery({
    queryKey: ["facts-review"],
    queryFn: apiClient.getWeeklyReviewFacts,
    enabled: tab === "review",
  });

  const wikiQuery = useQuery({
    queryKey: ["wiki-list"],
    queryFn: apiClient.listWiki,
    enabled: tab === "wiki",
  });

  const peopleQuery = useQuery({
    queryKey: ["people"],
    queryFn: apiClient.getPeople,
    enabled: tab === "people",
  });

  const onboardingQuery = useQuery({
    queryKey: ["onboarding"],
    queryFn: apiClient.getOnboardingStatus,
    enabled: tab === "onboarding",
  });

  const handleAddFact = async (e: FormEvent) => {
    e.preventDefault();
    if (!newFactText.trim()) {
      toast.error("Saisissez le texte du fait avant de l'ajouter.");
      return;
    }
    try {
      await apiClient.createFact({
        subject: "Tom",
        predicate: "note_manuelle",
        object: newFactText.trim().slice(0, 80),
        text: newFactText.trim(),
        category: newFactCat,
        confidence: 0.95,
        source: "web_ui",
        sensitivity: newFactSens,
      });
      setNewFactText("");
      toast.ok("Fait ajouté à la mémoire.");
      await qc.invalidateQueries({ queryKey: ["facts"] });
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const handleExport = async (format: "json" | "md") => {
    try {
      const data = await apiClient.exportMemory();
      const content = format === "json" ? JSON.stringify(data, null, 2) : data.markdown_bundle;
      const blob = new Blob([content], {
        type: format === "json" ? "application/json" : "text/markdown",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `jarvis-memory.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.ok(`Export ${format.toUpperCase()} téléchargé.`);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const activeWikiPage =
    wikiQuery.data?.find((w) => w.slug === selectedSlug) ?? wikiQuery.data?.[0];
  const effectiveWikiText = wikiDraft ?? activeWikiPage?.content ?? "";

  return (
    <section className="page" aria-label="Mémoire et Onboarding">
      <PageHeader
        title="Mémoire et Onboarding"
        description="Faits retenus par Jarvis, wiki personnel en Markdown, proches et questionnaire de profil."
        actions={
          <>
            <button type="button" className="btn" onClick={() => handleExport("json")}>
              <Icon name="download" size={15} />
              Export JSON
            </button>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => handleExport("md")}
            >
              <Icon name="download" size={15} />
              Export Markdown
            </button>
          </>
        }
      />

      <div className="tabs-bar">
        <Segmented<TabId>
          label="Sections de la mémoire"
          value={tab}
          onChange={setTab}
          options={[
            { value: "facts", label: "Faits" },
            { value: "wiki", label: "Wiki Markdown" },
            { value: "people", label: "Personnes" },
            { value: "review", label: "Revue hebdomadaire" },
            { value: "onboarding", label: "Questionnaire" },
          ]}
        />
      </div>

      {tab === "facts" && (
        <div className="stack">
          <div className="card stack">
            <div className="card__subhead">Ajouter un fait</div>
            <form className="add-fact" onSubmit={handleAddFact}>
              <input
                type="text"
                className="input"
                placeholder="Ex : Préfère les réponses concises le matin"
                aria-label="Nouveau fait"
                value={newFactText}
                onChange={(e) => setNewFactText(e.target.value)}
              />
              <select
                className="select"
                aria-label="Catégorie du nouveau fait"
                value={newFactCat}
                onChange={(e) => setNewFactCat(e.target.value)}
              >
                <option value="general">Général</option>
                <option value="identite">Identité</option>
                <option value="travail">Travail</option>
                <option value="gouts">Goûts</option>
                <option value="sante">Santé</option>
                <option value="finances">Finances</option>
              </select>
              <select
                className="select"
                aria-label="Sensibilité du nouveau fait"
                value={newFactSens}
                onChange={(e) => setNewFactSens(e.target.value as SensitivityLevel)}
              >
                <option value="normal">Normal</option>
                <option value="privé">Privé</option>
                <option value="très privé">Très privé</option>
              </select>
              <button type="submit" className="btn btn--primary" disabled={!newFactText.trim()}>
                <Icon name="plus" size={16} />
                Ajouter
              </button>
            </form>
          </div>

          <div className="card card--flush">
            <div className="card__toolbar">
              <div className="filters">
                <input
                  type="search"
                  className="input input--sm"
                  placeholder="Rechercher un fait…"
                  aria-label="Rechercher un fait"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <select
                  className="select select--sm"
                  aria-label="Filtrer par catégorie"
                  value={categoryFilter}
                  onChange={(e) => setCategoryFilter(e.target.value)}
                >
                  <option value="">Toutes catégories</option>
                  <option value="identite">Identité</option>
                  <option value="travail">Travail</option>
                  <option value="rythme">Rythme</option>
                  <option value="gouts">Goûts</option>
                  <option value="sante">Santé</option>
                  <option value="finances">Finances</option>
                  <option value="general">Général</option>
                </select>
                <select
                  className="select select--sm"
                  aria-label="Filtrer par sensibilité"
                  value={sensitivityFilter}
                  onChange={(e) => setSensitivityFilter(e.target.value)}
                >
                  <option value="">Toute sensibilité</option>
                  <option value="normal">Normal</option>
                  <option value="privé">Privé</option>
                  <option value="très privé">Très privé</option>
                </select>
              </div>
            </div>
            {factsQuery.isLoading && <Skeleton lines={4} />}
            {!factsQuery.isLoading && (factsQuery.data ?? []).length === 0 && (
              <EmptyState title="Aucun fait enregistré pour ces critères.">
                Ajoutez un fait ci-dessus ou élargissez la recherche.
              </EmptyState>
            )}
            {(factsQuery.data ?? []).length > 0 && (
              <ul className="list">
                {(factsQuery.data ?? []).map((fact) => {
                  const isEditing = editingFactId === fact.id;
                  return (
                    <li key={fact.id} className="row">
                      <div className="row__body">
                        {isEditing ? (
                          <form
                            className="cluster"
                            onSubmit={async (e) => {
                              e.preventDefault();
                              if (editingFactText.trim()) {
                                try {
                                  await apiClient.updateFact(fact.id, {
                                    text: editingFactText.trim(),
                                  });
                                  setEditingFactId(null);
                                  toast.ok("Fait modifié.");
                                  qc.invalidateQueries({ queryKey: ["facts"] });
                                  qc.invalidateQueries({ queryKey: ["facts-review"] });
                                } catch (err) {
                                  toast.error(errorMessage(err));
                                }
                              }
                            }}
                          >
                            <input
                              type="text"
                              className="input input--sm"
                              style={{ flex: 1 }}
                              value={editingFactText}
                              onChange={(e) => setEditingFactText(e.target.value)}
                              autoFocus
                            />
                            <button type="submit" className="btn btn--primary btn--sm">
                              Enregistrer
                            </button>
                            <button
                              type="button"
                              className="btn btn--ghost btn--sm"
                              onClick={() => setEditingFactId(null)}
                            >
                              Annuler
                            </button>
                          </form>
                        ) : (
                          <div>{fact.text}</div>
                        )}
                        <div className="row__meta">
                          <span className="badge">
                            {CATEGORY_LABELS[fact.category] ?? fact.category}
                          </span>
                          <span className="badge">{fact.sensitivity}</span>
                          <span className="mono">confiance {Math.round(fact.confidence * 100)}%</span>
                        </div>
                      </div>
                      {!isEditing && (
                        <div className="row__actions">
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            onClick={() => {
                              setEditingFactId(fact.id);
                              setEditingFactText(fact.text);
                            }}
                          >
                            Éditer
                          </button>
                          <ConfirmButton
                            onConfirm={async () => {
                              try {
                                await apiClient.deleteFact(fact.id);
                                toast.ok("Fait supprimé.");
                                qc.invalidateQueries({ queryKey: ["facts"] });
                                qc.invalidateQueries({ queryKey: ["facts-review"] });
                              } catch (err) {
                                toast.error(errorMessage(err));
                              }
                            }}
                          />
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}

      {tab === "wiki" && (
        <div className="wiki">
          <div className="wiki__pages" role="tablist" aria-label="Pages du wiki">
            {wikiQuery.isLoading && <Skeleton lines={3} />}
            {wikiQuery.isError && (
              <EmptyState title="Impossible de charger le wiki." />
            )}
            {(wikiQuery.data ?? []).map((p) => {
              const current = (activeWikiPage?.slug ?? selectedSlug) === p.slug;
              return (
                <button
                  key={p.slug}
                  type="button"
                  className="wiki__page"
                  aria-current={current ? "true" : undefined}
                  onClick={() => {
                    setSelectedSlug(p.slug);
                    setWikiDraft(p.content);
                  }}
                >
                  {p.slug}.md
                </button>
              );
            })}
          </div>

          <div className="card stack">
            <div className="card__head" style={{ marginBottom: 0 }}>
              <label htmlFor="wiki-editor" className="card__title">
                {activeWikiPage?.slug ?? selectedSlug}.md
              </label>
              <div className="cluster">
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => setWikiPreview((v) => !v)}
                >
                  {wikiPreview ? "Éditer" : "Aperçu"}
                </button>
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  onClick={async () => {
                    const slug = activeWikiPage?.slug ?? selectedSlug;
                    try {
                      await apiClient.putWiki(slug, effectiveWikiText);
                      setWikiDraft(null);
                      toast.ok(`Page ${slug}.md enregistrée.`);
                      qc.invalidateQueries({ queryKey: ["wiki-list"] });
                    } catch (err) {
                      toast.error(errorMessage(err));
                    }
                  }}
                >
                  Enregistrer la page
                </button>
              </div>
            </div>

            {wikiPreview ? (
              <pre
                style={{
                  margin: 0,
                  padding: "var(--sp-4)",
                  borderRadius: "var(--r-md)",
                  background: "var(--surface-2)",
                  whiteSpace: "pre-wrap",
                  fontFamily: "var(--font-sans)",
                  lineHeight: 1.6,
                }}
              >
                {effectiveWikiText}
              </pre>
            ) : (
              <textarea
                id="wiki-editor"
                className="textarea wiki__editor"
                rows={16}
                value={effectiveWikiText}
                onChange={(e) => setWikiDraft(e.target.value)}
              />
            )}
          </div>
        </div>
      )}

      {tab === "people" && (
        <div className="stack">
          <form
            className="card stack"
            onSubmit={async (e) => {
              e.preventDefault();
              const cleanName = personName.trim();
              if (!cleanName) {
                toast.error("Saisissez un nom avant d'ajouter une personne.");
                return;
              }
              try {
                await apiClient.createPerson({
                  name: cleanName,
                  relation: personRelation.trim() || "contact",
                  birthday: personBirthday.trim() || null,
                  contact_ref: personContact.trim() || null,
                  notes: personNotes.trim(),
                });
                setPersonName("");
                setPersonBirthday("");
                setPersonContact("");
                setPersonNotes("");
                toast.ok(`${cleanName} ajouté aux proches.`);
                qc.invalidateQueries({ queryKey: ["people"] });
              } catch (err) {
                toast.error(errorMessage(err));
              }
            }}
          >
            <h2 className="card__title">Ajouter un proche</h2>
            <div className="grid-2">
              <input
                type="text"
                className="input"
                placeholder="Nom complet"
                aria-label="Nom complet"
                value={personName}
                onChange={(e) => setPersonName(e.target.value)}
              />
              <input
                type="text"
                className="input"
                placeholder="Relation (ex : ami, famille, collegue)"
                aria-label="Relation"
                value={personRelation}
                onChange={(e) => setPersonRelation(e.target.value)}
              />
              <input
                type="text"
                className="input mono"
                placeholder="Anniversaire (YYYY-MM-DD, optionnel)"
                aria-label="Anniversaire"
                value={personBirthday}
                onChange={(e) => setPersonBirthday(e.target.value)}
              />
              <input
                type="text"
                className="input"
                placeholder="Contact (email / telephone)"
                aria-label="Contact"
                value={personContact}
                onChange={(e) => setPersonContact(e.target.value)}
              />
            </div>
            <div className="cluster">
              <input
                type="text"
                className="input"
                style={{ flex: 1 }}
                placeholder="Notes"
                aria-label="Notes"
                value={personNotes}
                onChange={(e) => setPersonNotes(e.target.value)}
              />
              <button type="submit" className="btn btn--primary">
                <Icon name="plus" size={16} />
                Ajouter
              </button>
            </div>
          </form>

          {peopleQuery.isLoading && <Skeleton lines={3} />}
          {peopleQuery.isError && (
            <EmptyState title="Impossible de charger les personnes." />
          )}
          <div className="grid-2">
            {(peopleQuery.data ?? []).map((person) => (
              <div key={person.id} className="card stack">
                <div className="cluster" style={{ justifyContent: "space-between" }}>
                  <strong>{person.name}</strong>
                  <div className="cluster">
                    <span className="badge">{person.relation}</span>
                    <ConfirmButton
                      onConfirm={async () => {
                        try {
                          await apiClient.deletePerson(person.id);
                          toast.ok(`${person.name} supprimé.`);
                          qc.invalidateQueries({ queryKey: ["people"] });
                        } catch (err) {
                          toast.error(errorMessage(err));
                        }
                      }}
                    />
                  </div>
                </div>
                {person.birthday && (
                  <div className="mono muted" style={{ fontSize: "0.82rem" }}>
                    Anniversaire : {person.birthday}
                  </div>
                )}
                {person.contact_ref && (
                  <div className="mono muted" style={{ fontSize: "0.82rem" }}>
                    {person.contact_ref}
                  </div>
                )}
                {person.notes && <div>{person.notes}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "review" && (
        <div className="card card--flush">
          <div className="card__head">
            <div>
              <h2 className="card__title">Revue hebdomadaire</h2>
              <p className="card__sub">
                Les 10 faits à la confiance la plus faible sont proposés pour confirmation ou
                suppression.
              </p>
            </div>
          </div>
          {(reviewQuery.data ?? []).length === 0 ? (
            <EmptyState title="Aucun fait à réviser cette semaine." />
          ) : (
            <ul className="list">
              {(reviewQuery.data ?? []).map((fact) => (
                <li key={fact.id} className="row">
                  <div className="row__body">
                    <div>{fact.text}</div>
                    <div className="row__meta">
                      <span className="badge">{fact.category}</span>
                      <span className="mono">confiance {fact.confidence.toFixed(2)}</span>
                    </div>
                  </div>
                  <div className="row__actions">
                    <button
                      type="button"
                      className="btn btn--ok btn--sm"
                      onClick={async () => {
                        await apiClient.updateFact(fact.id, { confidence: 0.98 });
                        toast.ok("Fait confirmé.");
                        qc.invalidateQueries({ queryKey: ["facts-review"] });
                      }}
                    >
                      Confirmer
                    </button>
                    <ConfirmButton
                      onConfirm={async () => {
                        try {
                          await apiClient.deleteFact(fact.id);
                          toast.ok("Fait supprimé.");
                          qc.invalidateQueries({ queryKey: ["facts-review"] });
                          qc.invalidateQueries({ queryKey: ["facts"] });
                        } catch (err) {
                          toast.error(errorMessage(err));
                        }
                      }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {tab === "onboarding" && onboardingQuery.isLoading && <Skeleton lines={4} />}
      {tab === "onboarding" && onboardingQuery.isError && (
        <EmptyState title="Impossible de charger le questionnaire." />
      )}
      {tab === "onboarding" && onboardingQuery.data && (
        <div className="stack">
          <div className="card stack">
            <div className="cluster" style={{ justifyContent: "space-between" }}>
              <div>
                <strong>
                  Progression : {onboardingQuery.data.completed_modules} /{" "}
                  {onboardingQuery.data.total_modules} modules complétés
                </strong>
              </div>
              {onboardingQuery.data.daily_question && (
                <span className="badge badge--accent">
                  Question du jour : {onboardingQuery.data.daily_question.prompt}
                </span>
              )}
            </div>
            <div className="progress" aria-hidden="true">
              <span
                style={{
                  width: `${
                    onboardingQuery.data.total_modules > 0
                      ? (onboardingQuery.data.completed_modules /
                          onboardingQuery.data.total_modules) *
                        100
                      : 0
                  }%`,
                }}
              />
            </div>
          </div>

          {onboardingQuery.data.modules.map((mod) => (
            <div key={mod.id} className="card stack">
              <div className="cluster" style={{ justifyContent: "space-between" }}>
                <h2 className="card__title">{mod.title}</h2>
                <span className={`badge ${mod.completed ? "badge--ok" : ""}`}>
                  {mod.answered_count}/{mod.total_count} {mod.completed ? "· complet" : ""}
                </span>
              </div>
              <p className="card__sub">{mod.description}</p>
              {mod.questions.map((q) => (
                <label key={q.id} className="field">
                  <span className="field__label">
                    {q.prompt}{" "}
                    {q.already_known && (
                      <span className="badge mono">déjà connu : {q.known_value}</span>
                    )}
                  </span>
                  <input
                    type="text"
                    className="input"
                    placeholder={q.placeholder}
                    value={onboardingDrafts[q.field_key] ?? q.known_value ?? ""}
                    onChange={(e) =>
                      setOnboardingDrafts((prev) => ({
                        ...prev,
                        [q.field_key]: e.target.value,
                      }))
                    }
                  />
                </label>
              ))}
              <div>
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={async () => {
                    const answers: Record<string, string> = {};
                    for (const q of mod.questions) {
                      const val = onboardingDrafts[q.field_key];
                      if (val && val.trim()) answers[q.field_key] = val.trim();
                    }
                    try {
                      await apiClient.answerOnboarding(mod.id, answers);
                      toast.ok(`Module ${mod.title} enregistré.`);
                      qc.invalidateQueries({ queryKey: ["onboarding"] });
                    } catch (err) {
                      toast.error(errorMessage(err));
                    }
                  }}
                >
                  Enregistrer ce module
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
