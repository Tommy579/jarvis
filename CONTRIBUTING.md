# Contribuer

Projet personnel, mais les contributions ciblées sont bienvenues. Le critère d'acceptation est
simple : **une modification arrive avec sa preuve** (test, sortie de commande, capture).

## Mise en place

```bash
git clone https://github.com/Tommy579/jarvis.git
cd jarvis
make install          # uv sync + frontend
make dev-core         # backend 127.0.0.1:8765
make dev-web          # interface Vite
```

## Avant d'ouvrir une pull request

```bash
env -u PYTHONPATH .venv/bin/python -m ruff check packages tests evals
env -u PYTHONPATH .venv/bin/python -m ruff format --check packages tests
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m mypy
PYTHONPATH=packages/shared:packages/core:packages/desktop:. .venv/bin/python -m pytest tests -q
make security
cd web && npm run build && npx vitest run
```

La CI exécute les mêmes étapes (`backend`, `frontend`, `install`, `docker`, `security`). Une PR
rouge n'est pas fusionnée.

## Règles de code

- Français pour les messages utilisateur, les commits et la documentation ; anglais toléré dans le
  code existant, sans mélange à l'intérieur d'un même fichier.
- Pas de nouvelle dépendance sans justification dans la description de la PR.
- Pas de refactor non lié au correctif : une PR = un sujet.
- Tout secret reste hors du dépôt (`.env` est ignoré, jamais commité).
- Les outils qui lisent du contenu externe (web, RSS, e-mail, presse-papiers, DOM) doivent passer
  par `wrap_untrusted_content` et rester en lecture seule sauf décision explicite.

## Commits

Style conventionnel : `fix(api): ...`, `feat(ui): ...`, `docs: ...`, `chore(ci): ...`.
Décrivez l'effet observable, pas la suite d'outils utilisés.

## Sécurité

Pour une vulnérabilité, **pas d'issue publique** : voir [SECURITY.md](SECURITY.md).