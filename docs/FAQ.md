# Depannage JARVIS 2.0

Toutes les commandes supposent que tu es a la racine du depot (ou de l'archive extraite).

## Reflexe n°1 : `jarvis doctor`

```bash
.venv/bin/jarvis doctor        # ou : .venv/bin/python -m jarvis_core.cli doctor
```

Il verifie Python, le venv, les permissions du `.env`, les cles LLM, l'ecriture du dossier de
donnees, l'integrite SQLite et le schema, la presence de `web/dist`, le port 8765 et l'espace
disque. Meme diagnostic dans l'interface : Configuration > Apparence > « Lancer le diagnostic ».

---

## « Jeton d'acces invalide ou manquant » (401)

Le service exige `JARVIS_API_TOKEN` des qu'il est defini. Le lanceur officiel s'en occupe :

```bash
make run     # genere le jeton au besoin, l'ecrit dans .env et l'injecte dans l'URL
```

Si tu lances uvicorn a la main, ouvre l'interface avec `?token=<valeur de .env>` ou saisis le
jeton sur l'ecran dedie. Pour un usage strictement local sur loopback, `JARVIS_ALLOW_INSECURE=1`
desactive la garde (deconseille).

## Le service ne demarre pas / port 8765 occupe

```bash
curl -s http://127.0.0.1:8765/api/health
ss -ltnp | grep 8765
tail -n 50 ~/.local/state/jarvis/jarvis-core.log
```

Une seule instance peut tourner (Qdrant/SQLite et le port). Arrete l'ancienne :

```bash
pkill -f 'uvicorn jarvis_core.api.server:app'
```

## Interface vide ou « web/dist absent »

```bash
cd web && npm ci && npm run build
# ou tout reinstalle : make install
```

Avec l'archive de distribution, l'interface est deja compilee : `JARVIS_FORCE_WEB_BUILD=1` force
une recompilation si tu as modifie le front.

## Aucun modele ne repond

1. Verifie qu'au moins une cle est presente dans `.env` (`OPENROUTER_API_KEY` ou `GEMINI_API_KEY`),
   ou qu'un Ollama local repond sur `OLLAMA_BASE_URL`.
2. `jarvis eval` doit passer 22/22 sans reseau : il teste le routage, pas les fournisseurs.
3. `curl -H "Authorization: Bearer $JARVIS_API_TOKEN" http://127.0.0.1:8765/api/models`.
4. Sans cle, JARVIS demarre en mode hors-ligne : c'est normal, il l'annonce dans l'interface.

## Sauvegarder et restaurer la memoire

```bash
jarvis backup now
jarvis backup list
jarvis backup restore jarvis-20261127T032000Z.db   # service arrete, sinon refus
jarvis backup restore <fichier> --force            # deconseille : base remplacee sous le service
```

Une sauvegarde automatique tourne chaque nuit a 03:20, et au demarrage si la derniere date de
plus de 24 h. Les fichiers sont dans `jarvis-data/backups/` (7 conserves par defaut,
`JARVIS_BACKUP_KEEP`). Une restauration cree toujours une copie `pre-restore-*.db` de la base
remplacee.

## Ou sont les journaux

| Fichier | Contenu |
|---|---|
| `~/.local/state/jarvis/launch.log` | Lanceur (menu applications, autostart) |
| `~/.local/state/jarvis/jarvis-core.log` | Service (uvicorn, routines, erreurs) |

Ils sont rotationnes a 5 Mo par le lanceur (`*.log.1` conserve).

## Docker : permission refusee sur les donnees

Le conteneur tourne avec l'UID 1000 et monte `${HOME}/jarvis-data` sur `/data`. Si ton utilisateur
a un autre UID, pointe un dossier accessible :

```bash
JARVIS_DATA_HOST=/chemin/accessible docker compose up -d --build
docker compose logs -f jarvis
```

## Micro ou camera inutilisable

- Linux : `portaudio` doit etre installe (le script d'installation s'en charge).
- La camera passe par `opencv-python` (`/dev/video0`) ; verifie les permissions du groupe `video`.
- Sous Wayland, la capture d'ecran depend de `xdg-desktop-portal` ; `jarvis doctor` signale
  l'absence de `web/dist` ou de dependances, pas les droits portal (verifie le journal du service).

## Mettre a jour

```bash
git pull
make install          # ou : bash install_linux.sh (archive)
```

Verifie ensuite la version affichee dans Configuration > Apparence, ou :

```bash
curl -H "Authorization: Bearer $JARVIS_API_TOKEN" http://127.0.0.1:8765/api/version
```

Le bouton « Verifier les mises a jour » interroge la derniere release GitHub (cache 6 h). Pour
desactiver cet appel reseau : `JARVIS_UPDATE_CHECK=0`.

## Reinitialiser l'interface (jeton, preferences)

Le jeton et les preferences sont dans le `localStorage` du navigateur. En cas de doute, ouvre
l'interface avec le jeton dans l'URL (`?token=...`) ou vide le stockage du site
`http://localhost:8765` depuis les outils de developpement.

## Signaler un bug

Joins la sortie de `jarvis doctor`, la version (`/api/version`) et les journaux ci-dessus. Les
donnees personnelles (memoire, wiki, `jarvis-data/`) ne doivent pas etre jointes.