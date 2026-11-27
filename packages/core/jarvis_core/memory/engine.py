"""Moteur de memoire : recherche hybride BM25+vecteurs, confidentialite, extraction, consolidation et assemblage de contexte."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis_shared.models import Episode, Fact, Message, Role, Sensitivity

from .embeddings import EmbeddingEngine
from .storage import MemoryStorage
from .wiki import MarkdownWiki

HIGHLY_PRIVATE_KEYWORDS = {
    "sante",
    "santé",
    "allergie",
    "medicament",
    "médicament",
    "traitement",
    "medecin",
    "médecin",
    "finance",
    "banque",
    "solde",
    "salaire",
    "impot",
    "impôt",
    "compte",
}

DEFAULT_PERSONA_PROMPT = (
    "Tu es Jarvis, l'assistant personnel d'JARVIS 2.0. "
    "Le modèle actif est le moteur principal choisi par l'utilisateur.\n\n"
    "RÈGLES IMPÉRATIVES DE COMMUNICATION (À APPLIQUER STRICTEMENT PAR TOUS LES MODÈLES) :\n"
    "1. Concision maximale : Réponds en 1 ou 2 phrases courtes mais grammaticalement complètes "
    "(sujet, verbe, complément, ton naturel ; jamais de mots-clés isolés ni de style télégraphique). "
    "Supprime tout préambule (« Bonjour Tom », « Voici le bilan », « Diagnostic terminé »), "
    "toute formule de politesse ou de remplissage, toute parenthèse explicative et toute liste à puces non sollicitée. "
    "Pendant les appels d'outils, annonce chaque étape intermédiaire en 1 seule phrase courte (10 à 15 mots max) "
    "décrivant uniquement l'action immédiate, sans résumer les étapes précédentes ni détailler les sorties techniques.\n"
    "2. Réponses longues ou denses : Si la réponse complète nécessite un développement important ou "
    "dépasse ce format concis, délivre le cœur essentiel de l'information et termine impérativement par '... [suite]' "
    "pour indiquer clairement à l'utilisateur qu'une suite détaillée est disponible sur demande.\n"
    "3. Direction et délégation aux sous-agents : Tu es le modèle principal choisi par l'utilisateur : "
    "ton rôle est uniquement de répondre directement aux questions simples et de diriger les travaux. "
    "Pour exécuter une commande, modifier un fichier ou coder, tu dois orchestrer et déléguer aux sous-agents en choisissant l'outil adapté à l'ampleur : "
    "utilise delegate_subagent (un seul sous-agent rapide) pour toute action ponctuelle (lancer une app/GUI/serveur, vérifier un port, "
    "exécuter une commande ou une modification ciblée), et réserve start_goal uniquement aux vrais projets longs ou multi-étapes "
    "nécessitant un plan, plusieurs sous-agents et une vérification finale (ou run_cli_subagent pour agy/hermes). "
    "Délégation à `agy` et `hermes` (`run_cli_subagent`) : `agy` et `hermes` sont des agents ingénieurs autonomes complets "
    "qui savent explorer le dépôt, lire les fichiers, concevoir le plan, éditer le code et vérifier leur travail. "
    "Ne pré-explore JAMAIS le code (`read_file`, `search_files`, `list_directory`) à leur place avant de les appeler, "
    "et ne leur dicte JAMAIS comment coder ni une liste de commandes shell (`sed`, `grep`, numéros de lignes) ou de micro-étapes : "
    "donne-leur directement un objectif global (le *quoi*, le *pourquoi*, `workdir` et les contraintes éventuelles) et laisse-les faire leur travail.\n"
    "4. Ordres de notification et politique : Les demandes d'alertes futures ou de changement de politique de notification/heures calmes "
    "passent obligatoirement par les outils notify_me et set_notification_policy ; ne promets jamais une notification sans avoir appelé ces outils.\n"
    "5. Autonomie navigateur & PC sans screenshots : Utilise browser_list_tabs, browser_snapshot, browser_click, browser_fill, browser_select "
    "(ou desktop_list_windows / desktop_snapshot / desktop_action sur Linux) pour piloter les pages et applis par arbre d'accessibilité. "
    'Pour une application web installée sur le PC (raccourci .desktop comme Apple Music, Marges ou Gemini), utilise `browser_launch` avec `app="Apple Music"` '
    "(ou l'URL de l'application) afin d'ouvrir ou réutiliser directement sa fenêtre dédiée (`--app`) plutôt qu'un onglet navigateur classique. "
    "Pour les tâches longues multi-étapes ou objectifs autonomes devant tourner jusqu'à vérification complète (code, recherche + synthèse, "
    "automatisation web multi-onglets, etc.), lance un objectif supervisé via start_goal plutôt que de t'arrêter à mi-chemin "
    "(ou run_autonomous_task pour une routine simple) et formule ta courte confirmation dans la MÊME réponse que l'appel d'outil "
    "afin que l'action démarre pendant que tu parles.\n"
    "6. URLs et ports locaux jamais devinés : Avant d'ouvrir une interface locale avec browser_launch ou browser_open_tab, "
    "ne devine jamais le port (ex: localhost:8000) ; vérifie d'abord le port réel dans le code du projet, les ports en écoute "
    "ou l'état de l'objectif en cours, et assure-toi que le serveur écoute avant d'ouvrir l'onglet."
)


class MemoryEngine:
    """Orchestre les 5 couches de memoire d'JARVIS 2.0."""

    TOKEN_BUDGET = 6000
    MAX_HISTORY_TURNS = 20

    def __init__(
        self,
        storage: MemoryStorage,
        wiki: MarkdownWiki,
        embedder: EmbeddingEngine | None = None,
        persona_prompt: str = "",
    ):
        self.storage = storage
        self.wiki = wiki
        self.embedder = embedder or EmbeddingEngine()
        self.persona_prompt = persona_prompt or DEFAULT_PERSONA_PROMPT

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-zA-Z0-9àâäéèêëîïôöùûüç]{2,}", (text or "").lower())

    @classmethod
    def _bm25_overlap_score(cls, query_tokens: list[str], doc_text: str) -> float:
        if not query_tokens:
            return 0.0
        doc_tokens = cls._tokenize(doc_text)
        if not doc_tokens:
            return 0.0
        doc_set = set(doc_tokens)
        matches = sum(1 for t in query_tokens if t in doc_set)
        return matches / len(query_tokens)

    @classmethod
    def query_touches_highly_private_topic(cls, query: str) -> bool:
        tokens = set(cls._tokenize(query))
        return bool(tokens & HIGHLY_PRIVATE_KEYWORDS)

    def save_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        text: str | None = None,
        category: str = "general",
        confidence: float = 0.9,
        source: str = "conversation",
        sensitivity: Sensitivity = Sensitivity.NORMAL,
    ) -> Fact:
        """Ajoute un fait et marque superseded_by sur tout fait actif contradictoire (meme sujet + predicat, objet different)."""
        natural_text = text or f"{subject} {predicate} {obj}"
        new_fact = Fact(
            subject=subject,
            predicate=predicate,
            object=obj,
            text=natural_text,
            category=category,
            confidence=confidence,
            source=source,
            sensitivity=sensitivity,
        )
        emb = self.embedder.embed(natural_text)

        # Detection de contradiction ou de mise a jour sur (subject, predicate)
        active = self.storage.list_active_facts()
        for existing, existing_emb, _ in active:
            if (
                existing.subject.lower() == subject.lower()
                and existing.predicate.lower() == predicate.lower()
            ):
                if existing.object.strip().lower() == obj.strip().lower():
                    # Doublon exact : mettre a jour la confiance si superieure
                    if confidence > existing.confidence:
                        existing.confidence = confidence
                        existing.updated_at = datetime.now(timezone.utc).isoformat()
                        self.storage.insert_fact(existing, emb)
                    return existing
                # Fait contredit : on conserve l'ancien avec superseded_by = new_fact.id
                self.storage.insert_fact(new_fact, emb)
                self.storage.mark_fact_superseded(existing.id, new_fact.id)
                return new_fact

        # Deduplication semantique immediate dans la meme categorie et le meme sujet
        for existing, existing_emb, _ in active:
            if (
                existing.category == category
                and existing.subject.lower() == subject.lower()
                and self.embedder.cosine_similarity(emb, existing_emb) >= 0.95
            ):
                if confidence > existing.confidence:
                    existing.confidence = confidence
                    existing.updated_at = datetime.now(timezone.utc).isoformat()
                    self.storage.insert_fact(existing, existing_emb)
                return existing

        self.storage.insert_fact(new_fact, emb)
        return new_fact

    def forget_topic(self, query: str) -> int:
        """Marque comme obsoletes tous les faits correspondant a un sujet ('Jarvis, oublie tout ce qui concerne X')."""
        q_lower = query.strip().lower()
        if not q_lower:
            return 0
        count = 0
        for fact, _, _ in self.storage.list_active_facts():
            if (
                q_lower in fact.text.lower()
                or q_lower in fact.subject.lower()
                or q_lower in fact.object.lower()
                or q_lower in fact.category.lower()
            ):
                self.storage.mark_fact_superseded(fact.id, "forgotten_by_user")
                count += 1
        return count

    _MEMORY_INTENT_KEYWORDS = (
        "souviens",
        "retiens",
        "rappelle",
        "mémorise",
        "memorise",
        "note ",
        "je m'appelle",
        "mon nom",
        "j'habite",
        "je travaille",
        "je préfère",
        "je prefere",
        "j'aime",
        "je déteste",
        "je deteste",
        "mon anniversaire",
        "allergi",
        "ma copine",
        "mon copain",
        "ma femme",
        "mon mari",
    )
    _EPHEMERAL_QUERY_RE = re.compile(
        r"\b(?:"
        r"m[eé]t[eé]o|quel temps|temp[eé]rature|quelle heure|quel jour|"
        r"minuteur|timer|r[eé]veil|alarme|"
        r"spotify|youtube|musique|playlist|mets le son|coupe le son|volume|"
        r"(?:nouveaux?|derniers?|re[cç]u|v[eé]rifie|lis|consulte)\s+(?:des\s+|mes\s+|les\s+)?"
        r"(?:mails?|emails?|messages?|rendez[- ]vous)|"
        r"mes\s+(?:mails?|emails?|messages?|rendez[- ]vous)|bo[iî]te de r[eé]ception|"
        r"mon agenda d[' ]aujourd|"
        r"traduis|combien font|calcule|explique[- ]moi|c[' ]est quoi|"
        r"coupe le record|arr[eê]te l[' ]enregistrement"
        r")\b",
        re.IGNORECASE,
    )
    _TRIVIAL_GREETINGS = {
        "bonjour",
        "salut",
        "coucou",
        "hello",
        "hi",
        "hey",
        "yo",
        "merci",
        "ok",
        "oui",
        "non",
        "stop",
        "chut",
        "test",
        "ping",
    }

    @classmethod
    def has_explicit_memory_intent(cls, user_text: str) -> bool:
        raw = (user_text or "").strip().lower()
        return any(kw in raw for kw in cls._MEMORY_INTENT_KEYWORDS)

    @classmethod
    def should_remember_turn(cls, user_text: str, assistant_text: str = "") -> bool:
        """Filtre les prompts triviaux ou ephemeres pour eviter de saturer la memoire."""
        raw = (user_text or "").strip().lower()
        if not raw or not (assistant_text or "").strip():
            return False
        if cls.has_explicit_memory_intent(raw):
            return True
        if cls._EPHEMERAL_QUERY_RE.search(raw):
            return False
        cleaned = re.sub(r"[^\w\sÀ-ÿ']", " ", raw).strip()
        words = cleaned.split()
        if len(cleaned) < 25 or len(words) <= 4:
            return False
        if words[0] in cls._TRIVIAL_GREETINGS and len(words) <= 6:
            return False
        return True

    def record_episode(
        self,
        kind: str,
        channel: str,
        summary: str,
        raw_ref: str | None = None,
    ) -> Episode:
        # Neutraliser tout bloc <untrusted> avant ecriture en memoire episodique
        clean_summary = re.sub(
            r"<untrusted\b[^>]*>.*?</untrusted>",
            "[contenu externe filtre]",
            summary or "",
            flags=re.DOTALL | re.IGNORECASE,
        )
        clean_summary = re.sub(
            r"</?\s*untrusted\b[^>]*>",
            "[untrusted]",
            clean_summary,
            flags=re.IGNORECASE,
        )
        ep = Episode(kind=kind, channel=channel, summary=clean_summary, raw_ref=raw_ref)
        emb = self.embedder.embed(clean_summary)
        return self.storage.insert_episode(ep, emb)

    def search(
        self,
        query: str,
        limit: int = 12,
        provider_trusted: bool = False,
    ) -> list[dict[str, Any]]:
        """Recherche hybride (BM25 + similarite cosinus + recence + frequence) avec filtrage de confidentialite."""
        q_vec = self.embedder.embed(query)
        q_tokens = self._tokenize(query)
        allow_highly_private = provider_trusted and self.query_touches_highly_private_topic(query)

        scored: list[tuple[float, dict[str, Any]]] = []
        for fact, emb, use_count in self.storage.list_active_facts():
            if fact.sensitivity == Sensitivity.HIGHLY_PRIVATE and not allow_highly_private:
                continue
            vec_score = self.embedder.cosine_similarity(q_vec, emb)
            bm25_score = self._bm25_overlap_score(
                q_tokens, f"{fact.subject} {fact.predicate} {fact.object} {fact.text}"
            )
            usage_boost = min(0.15, math.log1p(use_count) * 0.03)
            hybrid_score = (
                0.55 * bm25_score
                + 0.35 * max(0.0, vec_score)
                + 0.10 * fact.confidence
                + usage_boost
            )
            if hybrid_score > 0.05 or not q_tokens:
                scored.append(
                    (
                        hybrid_score,
                        {
                            "type": "fact",
                            "id": fact.id,
                            "text": fact.text,
                            "category": fact.category,
                            "confidence": fact.confidence,
                            "sensitivity": fact.sensitivity.value,
                            "score": round(hybrid_score, 4),
                        },
                    )
                )

        for ep, emb in self.storage.list_episodes(limit=100):
            if not allow_highly_private and self.query_touches_highly_private_topic(ep.summary):
                continue
            vec_score = self.embedder.cosine_similarity(q_vec, emb)
            bm25_score = self._bm25_overlap_score(q_tokens, ep.summary)
            hybrid_score = 0.55 * bm25_score + 0.45 * max(0.0, vec_score)
            if hybrid_score > 0.08:
                scored.append(
                    (
                        hybrid_score,
                        {
                            "type": "episode",
                            "id": ep.id,
                            "text": f"[{ep.ts[:16]}] ({ep.channel}) {ep.summary}",
                            "score": round(hybrid_score, 4),
                        },
                    )
                )

        scored.sort(key=lambda item: item[0], reverse=True)
        top_items = [item[1] for item in scored[:limit]]
        for item in top_items:
            if item["type"] == "fact":
                self.storage.mark_fact_used(item["id"])
        return top_items

    def get_weekly_review_candidates(self, limit: int = 10) -> list[Fact]:
        """Retourne les 10 faits actifs ayant la plus faible confiance pour validation hebdomadaire."""
        active = [f for f, _, _ in self.storage.list_active_facts()]
        active.sort(key=lambda f: (f.confidence, f.updated_at))
        return active[:limit]

    _EPHEMERAL_PREDICATE_RE = re.compile(
        r"^(?:a_?demand[eé]|demand[eé]|veut_?savoir|cherche|interroge|pose_?question|"
        r"souhaite_?voir|regarde|ecoute|vient_?de|a_?dit|a_?ecrit|teste|essaye)$",
        re.IGNORECASE,
    )
    _TRIVIAL_OBJECT_VALUES = {
        "",
        "rien",
        "aucun",
        "aucune",
        "inconnu",
        "inconnue",
        "n/a",
        "na",
        "none",
        "null",
    }

    async def extract_facts_from_turn_async(
        self,
        user_text: str,
        assistant_text: str,
        router: Any,
    ) -> list[Fact]:
        """Demande au role 'memory' d'extraire uniquement les faits durables d'un echange."""
        prompt = (
            "Analyse cet echange et extrais UNIQUEMENT les faits durables et verifiables "
            "concernant l'utilisateur (Tom) ou ses proches (identite, relations, preferences stables, "
            "habitudes, projets long terme, sante/allergies, dates importantes).\n"
            "NE MEMORISE JAMAIS : les questions ponctuelles (meteo, heure, code, traduction), "
            "les commandes d'outils (lancer musique, lire mails/agenda), les taches ephemeres d'une seule session, "
            "ni les suppositions.\n"
            "Format JSON strict : "
            '{"facts": [{"subject": "Tom", "predicate": "...", "object": "...", "text": "...", '
            '"category": "identite|famille|travail|sante|preferences|general", '
            '"confidence": 0.9, "sensitivity": "normal|prive|tres_prive"}]}.\n'
            'Dans le doute ou si aucun fait durable n\'est present, reponds {"facts": []}.\n\n'
            f"Utilisateur : {user_text}\nAssistant : {assistant_text}"
        )
        try:
            delta = await router.complete(
                role="memory",
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.0,
            )
            return self.apply_extracted_facts_json(delta.text)
        except Exception:
            return []

    def apply_extracted_facts_json(
        self, raw_json_text: str, min_confidence: float = 0.72
    ) -> list[Fact]:
        """Parse la reponse JSON du role memory et filtre les faits peu fiables ou ephemeres."""
        match = re.search(r"\{.*\}", raw_json_text or "", re.DOTALL)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

        created: list[Fact] = []
        for item in payload.get("facts") or []:
            if not isinstance(item, dict):
                continue
            subj = str(item.get("subject") or "Tom").strip()
            pred = str(item.get("predicate") or "").strip()
            obj = str(item.get("object") or "").strip()
            if not pred or not obj:
                continue
            if self._EPHEMERAL_PREDICATE_RE.match(pred.replace(" ", "_")):
                continue
            if obj.lower() in self._TRIVIAL_OBJECT_VALUES:
                continue
            try:
                conf = float(item.get("confidence") if item.get("confidence") is not None else 0.85)
            except (TypeError, ValueError):
                conf = 0.85
            if conf < min_confidence:
                continue
            sens_raw = str(item.get("sensitivity") or "normal").lower()
            if sens_raw in ("tres_prive", "très_privé", "tres prive"):
                sens = Sensitivity.HIGHLY_PRIVATE
            elif sens_raw in ("prive", "privé"):
                sens = Sensitivity.PRIVATE
            else:
                sens = Sensitivity.NORMAL

            fact = self.save_fact(
                subject=subj,
                predicate=pred,
                obj=obj,
                text=item.get("text"),
                category=str(item.get("category") or "general"),
                confidence=conf,
                source="conversation_extraction",
                sensitivity=sens,
            )
            created.append(fact)
        return created

    def consolidate_nightly(self, now: datetime | None = None) -> dict[str, Any]:
        """Consolidation nocturne (3h) : deduplication, episode journalier, mise a jour wiki + commit git, purge >90j."""
        ref_now = now or datetime.now(timezone.utc)
        active = self.storage.list_active_facts()

        # 1. Fusion des quasi-doublons (partitionne par categorie + sujet pour eviter le O(n^2) global)
        merged_count = 0
        seen: list[tuple[Fact, list[float]]] = []
        seen_by_bucket: dict[tuple[str, str], list[tuple[Fact, list[float]]]] = {}
        for fact, emb, _ in active:
            bucket_key = (fact.category, fact.subject.lower())
            bucket = seen_by_bucket.setdefault(bucket_key, [])
            duplicate_of: Fact | None = None
            for prev_fact, prev_emb in bucket:
                if self.embedder.cosine_similarity(emb, prev_emb) > 0.96:
                    duplicate_of = prev_fact
                    break
            if duplicate_of is not None:
                self.storage.mark_fact_superseded(fact.id, duplicate_of.id)
                merged_count += 1
            else:
                entry = (fact, emb)
                bucket.append(entry)
                seen.append(entry)

        # 2. Regeneration de moi.md a partir des faits actifs non tres prives
        by_category: dict[str, list[str]] = {}
        for fact, _ in seen:
            if fact.sensitivity == Sensitivity.HIGHLY_PRIVATE:
                continue
            by_category.setdefault(fact.category, []).append(f"- {fact.text}")

        moi_lines = ["# Profil de Tom", ""]
        for cat, lines in sorted(by_category.items()):
            moi_lines.append(f"## {cat.capitalize()}")
            moi_lines.extend(lines[:25])
            moi_lines.append("")
        self.wiki.write_page(
            "moi.md",
            "\n".join(moi_lines).strip() + "\n",
            commit_message=f"Consolidation nocturne {ref_now.strftime('%Y-%m-%d')}",
        )

        # 3. Purge des episodes bruts de plus de 90 jours
        cutoff_90d = (ref_now - timedelta(days=90)).isoformat()
        purged = self.storage.purge_old_raw_episodes(cutoff_90d)

        return {
            "merged_duplicates": merged_count,
            "active_facts": len(seen),
            "purged_episodes": purged,
        }

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text or "") // 4)

    def assemble_context(
        self,
        query: str,
        history: list[Message],
        live_state: dict[str, Any] | None = None,
        provider_trusted: bool = False,
    ) -> list[Message]:
        """Assemble le contexte dans l'ordre optimal pour le cache de prompt sous un budget de 6 000 tokens."""
        rules_md = self.wiki.read_page("rules.md").strip()
        stable_system = "\n\n".join(
            part for part in [self.persona_prompt.strip(), rules_md] if part
        )

        moi_md = self.wiki.read_page("moi.md").strip()
        if len(moi_md) > 4000:
            moi_md = moi_md[:4000] + "\n[...]"

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        state_dict = {"datetime": now_iso, **(live_state or {})}
        state_block = "### Etat courant\n" + "\n".join(f"- {k}: {v}" for k, v in state_dict.items())

        recalled = self.search(query=query, limit=12, provider_trusted=provider_trusted)
        memory_lines = [f"- ({item['type']}) {item['text']}" for item in recalled]
        memory_block = (
            "### Souvenirs pertinents\n" + "\n".join(memory_lines)
            if memory_lines
            else "### Souvenirs pertinents\nAucun souvenir specifique."
        )

        dynamic_context = f"{moi_md}\n\n{state_block}\n\n{memory_block}".strip()
        # Respect du budget de 6 000 tokens hors historique
        max_chars = self.TOKEN_BUDGET * 4
        if len(stable_system) + len(dynamic_context) > max_chars:
            allowed_dynamic = max(1000, max_chars - len(stable_system))
            dynamic_context = dynamic_context[:allowed_dynamic]

        assembled: list[Message] = [
            Message(role=Role.SYSTEM, content=stable_system),
            Message(role=Role.SYSTEM, content=dynamic_context),
        ]

        # Fenetre glissante au-dela de 20 tours
        max_msgs = self.MAX_HISTORY_TURNS * 2
        if len(history) > max_msgs:
            older = history[:-max_msgs]
            recent = history[-max_msgs:]
            summary_snippets = [
                f"{m.role.value}: {m.content[:80]}" for m in older[-10:] if m.content
            ]
            assembled.append(
                Message(
                    role=Role.SYSTEM,
                    content="Resume des echanges precedents :\n" + "\n".join(summary_snippets),
                )
            )
            assembled.extend(recent)
        else:
            assembled.extend(history)

        return assembled
