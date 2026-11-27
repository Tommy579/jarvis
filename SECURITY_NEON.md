# Brief sécurité — Gemini 4 Neon (injection de prompt, navigateur, Telegram, frontend)

> Lis ce document en entier avant de toucher au code. Tu travailles sur une copie locale de
> JARVIS 2.0. Deux autres modèles auditent l'API/l'exécution (Carbon) et les données/l'infra
> (Opus) : **ne sors pas de ton périmètre**.

## 1. Contexte

Jarvis ingère du contenu non fiable : pages web, mails, transcriptions, OCR, flux. Ce contenu
finit dans le contexte du modèle, qui peut ensuite appeler des outils (terminal, fichiers,
navigateur piloté par CDP). La défense actuelle consiste à encadrer le contenu externe dans
`<untrusted>` (`security.wrap_untrusted_content`) et à l'annoncer dans le persona. Toute fuite
dans cette chaîne est critique.

L'interface web (React/TS) rend du Markdown, des messages, des artefacts SVG/3D et stocke le
jeton API dans le `localStorage`. Le canal Telegram accepte des commandes de l'utilisateur.

## 2. Ta mission

Trouver et corriger les vulnérabilités d'injection (prompt, HTML/SVG, commandes) et de fuite de
données dans la chaîne contenu externe → modèle → outils → interface. Pour chaque hypothèse :
vérifier, prouver par un test ou une reproduction, corriger, revérifier.

## 3. Périmètre (branch `sec/neon-injection`)

- `packages/core/jarvis_core/security.py` : **uniquement** `wrap_untrusted_content` et ses
  appelants (pas la partie SSRF/HMAC, réservée à Carbon).
- Tous les points d'ingestion : `memory/engine.py`, `tools/browser.py`, `integrations/*`,
  `channels/telegram.py`, `runtime.py` (partie contenu externe).
- `web/src/` : rendu (`RichText.tsx`, `ConversationPage.tsx`), artefacts
  (`VisualArtifactWindow.tsx`), stockage du jeton (`lib/authToken.ts`), CSP et `index.html`.
- `packages/core/jarvis_core/tools/browser.py` (navigation, évaluation JS, téléchargements).
- `tests/` : nouveaux `tests/test_security_injection.py`, tests frontend dans `web/src/tests/`.

Hors périmètre : auth API, approbations, MCP, OAuth, OAuth callbacks, webhooks, SQLite,
Docker/installation. Signale, ne modifie pas.

## 4. Commandes de référence

```bash
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check packages tests
.venv/bin/python -m ruff format --check packages tests
cd web && npm run build && npx vitest run && npx playwright test --project=mock
```

## 5. Règles de correction

- Pas de changement de rendu visible non lié à la sécurité ; pas d'animation ajoutée.
- Préserve les contrats publics (protocole WS, modèles partagés, types générés).
- Toute correction s'accompagne d'un test qui échoue avant et passe après.
- Ne touche jamais `.env`, `jarvis-data/`, ni aux secrets. Pas de commande destructive.

## 6. Hypothèses à vérifier

1. **Contournement du bac à sable `untrusted`.** Recense tous les points d'ingestion (web,
   mail, OCR, audio, RSS, MCP, tool results) et vérifie que chacun passe par
   `wrap_untrusted_content`. Un seul chemin oublié suffit à neutraliser la défense. Vérifie aussi
   la robustesse de la regex face à `</untrusted >`, casse, attributs, ou contenu imbriqué.
2. **Résultats d'outils réinjectés.** Une sortie d'outil (page web, fichier lu) revient-elle au
   modèle sans encadrement ? Si oui, c'est le vecteur principal : corriger au niveau du runtime.
3. **Rendu Markdown/HTML.** Cherche `dangerouslySetInnerHTML`, `innerHTML`, `eval(`, `new Function`.
   Vérifie le traitement des liens `javascript:`, `data:`, des images distantes (fuite d'IP) et
   des balises HTML dans les messages.
4. **Artefacts SVG.** `svg_path` est rendu dans un attribut `d` et les labels comme texte. Vérifie
   qu'aucune valeur ne peut casser la structure SVG ou déclencher un chargement externe
   (`url(...)`, `<image>`, entités). La couleur est déjà validée côté backend ; vérifie le reste.
5. **Évaluation JS navigateur.** `browser_eval` exécute du JS dans la page : vérifie les
   approvals, la portée (onglet courant), l'accès aux cookies, et ce qu'un contenu hostile peut
   faire exécuter au modèle. Vérifie aussi `file://`, `javascript:` et les téléchargements.
6. **CDP exposé.** Le port de débogage Chromium écoute-t-il au-delà de la loopback ? Une page
   hostile peut-elle s'y connecter ? Vérifie `--remote-debugging-port` et l'authentification.
7. **Telegram.** Vérifie que `allowed_chat_id` est contrôlé sur **tous** les handlers (messages,
   callbacks, commandes, fichiers), le `parse_mode` (HTML injecté par le modèle), et les tailles
   de messages. Un compte non autorisé ne doit rien déclencher.
8. **Jeton dans le navigateur.** `localStorage` + CSP `script-src 'self' 'unsafe-inline'` :
   évalue l'impact d'une XSS et si `'unsafe-inline'` est réellement nécessaire (Vite en prod).
9. **Journaux.** Cherche les endroits où un jeton, un mot de passe, un contenu personnel ou une
   transcription complète est loggé (`logger.*`), y compris en cas d'erreur HTTP.
10. **Fichiers téléchargés.** Le toolkit navigateur peut-il écrire hors d'un répertoire dédié
    (nom de fichier contrôlé par la page, `../`) ?

## 7. Format du rapport

Écris `docs/security/neon-report.md` :

```markdown
| ID | Sévérité | Fichier:ligne | Vecteur | Reproduction | Correctif | Statut |
|----|----------|---------------|---------|--------------|-----------|--------|
| N-01 | Critique | security.py:17 | page web -> prompt | test_security_injection.py::test_x | ... | corrigé |
```

Pour chaque entrée : contenu hostile utilisé, chemin exact jusqu'à l'action, preuve (test ou
capture), correctif, risque résiduel. Termine par « ce que je n'ai pas pu vérifier ».

## 8. Definition of done

- Tests ajoutés (backend et frontend), suite verte, ruff et vitest verts.
- Un commit par correctif (`fix(security): ...`), messages en français.
- Rapport écrit, faits vérifiés séparés des hypothèses non concluantes.
- Aucune modification hors périmètre, aucun secret affiché.