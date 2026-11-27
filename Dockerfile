# syntax=docker/dockerfile:1
# Image de production JARVIS 2.0 : interface web compilee + service jarvis-core.

# ── Etape 1 : compilation de l'interface React ─────────────────────────────
FROM node:26-slim AS web

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


# ── Etape 2 : runtime Python ───────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache \
    PATH=/app/.venv/bin:$PATH \
    JARVIS_DATA_DIR=/data

# uv epingle : le lock (revision 3) a ete genere avec cette version.
RUN pip install --no-cache-dir "uv==0.12.3"

WORKDIR /app

# Manifestes seuls d'abord : la couche de dependances reste en cache tant que
# les lockfiles ne changent pas.
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY packages/core/pyproject.toml packages/core/
COPY packages/desktop/pyproject.toml packages/desktop/
RUN uv sync --frozen --no-dev --no-install-project

COPY packages ./packages
COPY config ./config
RUN uv sync --frozen --no-dev

# Interface web deja compilee par l'etape 1 (le core la sert depuis web/dist).
COPY --from=web /web/dist ./web/dist

# Utilisateur non-root et repertoire de donnees persistant (0700 : memoire personnelle).
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin jarvis \
    && mkdir -p /data \
    && chown -R jarvis:jarvis /app /data \
    && chmod 700 /data
USER jarvis

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).status == 200 else 1)"

# 0.0.0.0 est necessaire dans le conteneur (le port est publie sur 127.0.0.1 par compose).
# umask 077 : la base, le wiki et les sauvegardes du volume /data ne sont pas lisibles
# par les autres utilisateurs de l'hote (sinon 0644 sur le montage).
CMD ["sh", "-c", "umask 077 && exec uvicorn jarvis_core.api.server:app --host 0.0.0.0 --port 8765"]
