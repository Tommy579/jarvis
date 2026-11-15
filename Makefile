# Makefile pour OMI 2.0 (Open Mind Interface)

.PHONY: install run dev-core dev-web test lint codegen-types build-release clean

ifeq ($(OS),Windows_NT)
VENV_BIN := .venv\Scripts
PYTHON := $(VENV_BIN)\python.exe
PIP := $(VENV_BIN)\pip.exe
RM := rmdir /s /q
else
VENV_BIN := .venv/bin
PYTHON := $(VENV_BIN)/python
PIP := $(VENV_BIN)/pip
RM := rm -rf
endif

install:
	@echo "Synchronisation du workspace uv (Python 3.12)..."
	uv sync --python 3.12 || (python3 -m venv .venv && $(PIP) install -r requirements.txt)
	@if [ -d web ] && [ -f web/package.json ]; then cd web && npm install; fi

run:
	@echo "Demarrage de l'assistant legacy OMI..."
	$(PYTHON) main.py

dev-core:
	@echo "Demarrage du service omi-core sur 127.0.0.1:8765..."
	PYTHONPATH=packages/shared:packages/core:packages/desktop:. $(PYTHON) -m uvicorn omi_core.api.server:app --host 127.0.0.1 --port 8765 --reload

dev-web:
	@echo "Demarrage de l'interface web React..."
	cd web && npm run dev

codegen-types:
	@echo "Generation des types TypeScript depuis omi_shared..."
	PYTHONPATH=packages/shared:. $(PYTHON) -m omi_shared.codegen --out web/src/types/generated.ts

test:
	@echo "Lancement des tests Python..."
	PYTHONPATH=packages/shared:packages/core:packages/desktop:. $(PYTHON) -m pytest tests
	@if [ -d web ] && [ -f web/package.json ]; then echo "Lancement des tests Vitest..." && cd web && npm test; fi

lint:
	@echo "Verification ruff sur packages/ et tests/..."
	$(PYTHON) -m ruff check packages tests

build-release:
	@echo "Lancement de la compilation de release..."
	$(PYTHON) build_release.py

clean:
	@echo "Nettoyage des dossiers de build..."
	$(RM) build dist web/dist
