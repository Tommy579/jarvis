# Makefile pour JARVIS 2.0 (Open Mind Interface)

.PHONY: install install-full run dev-core dev-web test test-e2e test-browser lint format codegen-types clean

ifeq ($(OS),Windows_NT)
VENV_BIN := .venv\Scripts
PYTHON := $(VENV_BIN)\python.exe
RM := rmdir /s /q
else
VENV_BIN := .venv/bin
PYTHON := $(VENV_BIN)/python
RM := rm -rf
endif

# uv n'est pas toujours dans le PATH des shells non interactifs.
UV := $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)

install:
	@echo "Synchronisation du workspace uv (Python 3.12)..."
	$(UV) sync --python 3.12
	@if [ -d web ] && [ -f web/package.json ]; then cd web && npm install; fi

# Extras optionnels : transcription locale (faster-whisper, onnxruntime) et
# recherche semantique dense (fastembed, sqlite-vec).
install-full:
	@echo "Synchronisation du workspace uv avec les extras voice et memory..."
	$(UV) sync --python 3.12 --all-extras
	@if [ -d web ] && [ -f web/package.json ]; then cd web && npm install; fi

run:
	@echo "Demarrage de JARVIS 2.0..."
	bash scripts/launch_jarvis_v2.sh

dev-core:
	@echo "Demarrage du service jarvis-core sur 127.0.0.1:8765..."
	PYTHONPATH=packages/shared:packages/core:packages/desktop:. $(PYTHON) -m uvicorn jarvis_core.api.server:app --host 127.0.0.1 --port 8765 --reload

dev-web:
	@echo "Demarrage de l'interface web React..."
	cd web && npm run dev

codegen-types:
	@echo "Generation des types TypeScript depuis jarvis_shared..."
	PYTHONPATH=packages/shared:. $(PYTHON) -m jarvis_shared.codegen --out web/src/types/generated.ts

test:
	@echo "Lancement des tests Python..."
	PYTHONPATH=packages/shared:packages/core:packages/desktop:. $(PYTHON) -m pytest tests
	@if [ -d web ] && [ -f web/package.json ]; then echo "Lancement des tests Vitest..." && cd web && npm test; fi

test-e2e:
	@echo "Tests bout-en-bout Playwright (mock + jarvis-core reel)..."
	cd web && npx playwright test

test-browser:
	@echo "Test bout-en-bout du pilotage navigateur (Chromium requis)..."
	JARVIS_BROWSER_E2E=1 PYTHONPATH=packages/shared:packages/core:packages/desktop:. $(PYTHON) -m pytest tests/test_browser_cdp_e2e.py -q

lint:
	@echo "Verification ruff sur packages/, tests/ et evals/..."
	$(PYTHON) -m ruff check packages tests evals packaging
	$(PYTHON) -m ruff format --check packages tests evals packaging

format:
	@echo "Formate le code Python (et applique les corrections sures de ruff)..."
	$(PYTHON) -m ruff check packages tests evals packaging --fix
	$(PYTHON) -m ruff format packages tests evals packaging

clean:
	@echo "Nettoyage des dossiers de build..."
	$(RM) build dist web/dist packages/*/build packages/*/*.egg-info
