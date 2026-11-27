# Installation de JARVIS 2.0

Guide pas à pas pour Linux (installation guidée) et Windows (exécutable pré-construit ou
installation depuis les sources). Le projet est un workspace Python 3.12 (`packages/*`) plus
une interface React (`web/`).

---

## 1. Ce qu'il faut avant de commencer

| Composant | Linux | Windows |
|---|---|---|
| Python | 3.12 (fourni par le script si absent) | 3.12+ |
| Node.js | 20+ (compilation de l'interface) | 20+ |
| `uv` | recommandé, installé par le script | facultatif |
| Audion | `portaudio` (micro/casque) | inclus |

Vérification rapide :

```bash
python3 --version    # 3.12.x
node --version       # v20 ou plus
uv --version         # optionnel
```

Sur Linux, la caméra et le micro reposent sur `opencv-python` et `sounddevice` : le paquet
système `portaudio` doit être présent (le script d'installation s'en occupe).

---

## 2. Installation guidée sur Linux (recommandé)

### Option rapide — archive précompilée (sans git ni Node)

```bash
# depuis la page Releases du dépôt GitHub, télécharger l'archive ET SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing   # doit afficher « OK »
tar -xzf jarvis-2.0.2-linux-x86_64.tar.gz
cd jarvis-2.0.2
bash install_linux.sh
```

Si `sha256sum` n'affiche pas `OK`, ne pas installer l'archive : retélécharger ou signaler le
problème (voir `SECURITY.md`).

L'interface web est déjà compilée dans l'archive : seuls Python 3.12 et `uv` sont nécessaires.
Pour forcer une recompilation depuis les sources : `JARVIS_FORCE_WEB_BUILD=1 bash install_linux.sh`.

### Option complète — depuis les sources

### Étape 1 — Récupérer le projet

```bash
git clone https://github.com/Tommy579/jarvis.git ~/Documents/jarvis
cd ~/Documents/jarvis
```

### Étape 2 — Lancer l'installeur

```bash
bash install_linux.sh
```

Le script :

1. crée `.env` depuis `.env.example` ;
2. installe les dépendances système (Arch ou Debian/Ubuntu) : `portaudio`, `playerctl`,
   `nodejs`, `npm`, `uv` ;
3. crée le virtualenv `.venv` et installe le workspace (`uv sync`, ou `pip install -e
   packages/...` si `uv` est absent) ;
4. compile l'interface (`npm ci && npm run build`) dans `web/dist` ;
5. installe un raccourci dans le menu applications et l'autostart de session.

Pour fournir la clé Gemini pendant l'installation :

```bash
bash install_linux.sh "MA_CLE_GEMINI"
```

### Étape 3 — Renseigner les clés LLM

Éditez `.env` et mettez **au moins une** clé :

```ini
OPENROUTER_API_KEY=sk-or-...
# ou
GEMINI_API_KEY=AIza...
```

Sans clé, le service démarre quand même en **mode hors-ligne** (il répond qu'il n'a pas de
modèle configuré, aucune requête réseau n'est faite).

### Étape 4 — Démarrer

```bash
make run
```

Ce que fait `make run` : démarre `jarvis-core` sur `127.0.0.1:8765` s'il ne tourne pas déjà,
récupère (ou génère) `JARVIS_API_TOKEN` dans `.env`, puis ouvre l'interface dans une fenêtre
Chromium en mode application avec le jeton déjà injecté. Au premier lancement, un jeton
aléatoire est écrit dans `.env` : c'est normal, il ne faut pas le changer sans redémarrer le
service.

### Étape 5 — Vérifier que tout fonctionne

```bash
# le service répond
curl -s http://127.0.0.1:8765/api/health
# {"status":"ok","service":"jarvis-core"}

# le jeton est bien appliqué (401 sans jeton, 200 avec)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/api/models
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $(grep ^JARVIS_API_TOKEN= .env | cut -d= -f2)" http://127.0.0.1:8765/api/models

# la suite de tests (doit être verte)
make test

# diagnostic complet : python, venv, .env, cles, base SQLite, disque, port, interface
.venv/bin/jarvis doctor
```

### Installation manuelle (sans script)

```bash
uv sync --python 3.12            # ou : uv sync --python 3.12 --all-extras (voix + mémoire dense)
(cd web && npm ci && npm run build)
cp .env.example .env             # puis renseigner une clé LLM
make run
```

---

## 3. Windows

### Option A — Exécutable pré-construit (le plus simple)

1. Ouvrez l'onglet **Actions** du dépôt GitHub → workflow **Release Windows (exe)** →
   **Run workflow** (ou téléchargez l'archive si une release existe déjà).
2. Téléchargez `JARVIS-windows-x64.zip` depuis les artefacts / la release.
3. Décompressez où vous voulez (le dossier contient `jarvis.exe`, `config/`, `web/dist/`).
4. Lancez `jarvis.exe`. Au premier démarrage il crée `.env` et `jarvis-data/` à côté du
   binaire, génère le jeton, ouvre l'interface et démarre le service.
5. Renseignez une clé LLM dans `.env`, puis relancez `jarvis.exe`.

En cas d'échec silencieux (pas de fenêtre), double-cliquez sur `JARVIS-debug.cmd` : il lance
le binaire dans une console visible avec les journaux.

Aucune installation de Python n'est nécessaire pour cette option.

### Option B — Depuis les sources

```powershell
git clone https://github.com/Tommy579/jarvis.git "$HOME\Documents\jarvis"
cd "$HOME\Documents\jarvis"
powershell -ExecutionPolicy Bypass -File install_windows.ps1
```

Le script configure `.env`, installe le workspace Python (via `uv` ou `pip`), compile
l'interface et crée un raccourci sur le Bureau. Lancement :

```powershell
powershell -ExecutionPolicy Bypass -File scripts\launch_jarvis_v2.ps1
```

### Construire l'exécutable soi-même

PyInstaller ne produit **pas** d'exécutable Windows depuis Linux : la construction doit se
faire sur Windows (ou sur un runner Windows GitHub Actions).

Sur un poste Windows :

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Options utiles : `-SkipInstall` (dépendances déjà installées), `-SkipFrontend` (interface
déjà compilée), `-NoZip`.

Le script produit :

```
dist\JARVIS\jarvis.exe     binaire fenêtré (aucune console)
dist\JARVIS\config\        modèles, MCP, persona
dist\JARVIS\web\dist\      interface compilée
dist\JARVIS\.env.example   modèle de configuration
dist\JARVIS-windows-x64.zip
```

Sans machine Windows, poussez un tag et laissez la CI construire l'archive :

```bash
git tag v2.0.2 && git push origin v2.0.2
```

Le workflow `Release Windows (exe)` compile, **vérifie que `jarvis.exe` répond sur
`/api/health`**, publie l'artefact et l'attache à la release.

Points techniques déjà en place pour le binaire :

- `packaging/launcher.py` démarre uvicorn en tâche de fond, attend `/api/health`, ouvre
  Chrome/Edge en mode application avec le jeton, et arrête proprement le serveur à la fermeture.
- `JARVIS_APP_ROOT` fait écrire `.env`, `config/` et `jarvis-data/` **à côté de l'exe** (et non
  dans le dossier temporaire du bundle PyInstaller).
- `config/` et `web/dist` sont livrés à côté du binaire : ils restent modifiables sans rebuild.

---

## 4. Où sont mes données

| Chemin | Contenu |
|---|---|
| `.env` | clés API, jeton, réglages du service |
| `jarvis-data/jarvis.db` | conversations, faits, alertes, approbations, coûts |
| `jarvis-data/wiki/` | pages Markdown éditables (`moi.md`, `rules.md`, …) |

Sauvegarde : copiez `jarvis-data/` et `.env` (jamais de `.db` ni de `.env` dans un dépôt Git).

---

## 5. Dépannage

**`JARVIS_API_TOKEN manquant` au démarrage**
Lancez via `make run` (le script génère le jeton), ou ajoutez `JARVIS_API_TOKEN=...` dans
`.env`. `JARVIS_ALLOW_INSECURE=1` n'est prévu que pour un usage local sur loopback.

**L'interface affiche « Jeton d'accès requis »**
Le service exige un jeton que le navigateur n'a pas encore. Saisissez la valeur de
`JARVIS_API_TOKEN` (dans `.env`) dans l'écran d'appairage ; ou relancez `make run`, qui
l'injecte automatiquement.

**Toutes les requêtes répondent 401**
Vérifiez que le jeton du navigateur et celui du service sont identiques : changer
`JARVIS_API_TOKEN` dans `.env` impose de redémarrer `jarvis-core` **et** de re-saisir le jeton
dans l'interface.

**`Address already in use: 8765`**
Un service tourne déjà (c'est normal si `make run` a été lancé deux fois). Identifier le
processus avec `pgrep -af "uvicorn jarvis_core"` puis l'arrêter, ou définir `JARVIS_PORT=8790`.

**Pas de son / pas de micro**
Installez `portaudio` (`sudo pacman -S portaudio` ou `sudo apt install libportaudio2`), puis
vérifiez que le micro n'est pas en pause dans l'interface (page d'accueil, capteur « micro »).

**Aucun modèle configuré / mode hors-ligne**
`.env` ne contient aucune clé exploitable. Une clé Vertex sans `GOOGLE_CLOUD_PROJECT` est
ignorée : le mode hors-ligne s'applique.

**La CI échoue sur `ruff`**
Lancez `make format` puis `make lint` : le formatage est vérifié en plus du lint.

---

## 6. Développement

```bash
make install          # dépendances Python + frontend
make dev-core         # service avec rechargement à chaud (127.0.0.1:8765)
make dev-web          # interface Vite avec proxy (localhost:5173)
make test             # pytest + vitest
make test-e2e         # Playwright (mock + core réel)
make lint             # ruff check + format --check
make install-full     # extras : transcription locale (faster-whisper) et embeddings denses
```
