"""Tests du chemin d'authentification documente (README + scripts/launch_jarvis_v2.sh).

Le defaut constate : avec `JARVIS_API_TOKEN` defini comme le README le demande, toutes les
routes `/api/*` repondaient 401 et l'interface n'offrait aucun moyen de saisir le jeton ;
a l'inverse le lanceur officiel forcait `JARVIS_ALLOW_INSECURE=1`, donc aucun jeton n'etait
jamais utilise.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jarvis_core.api.server import JarvisCoreState, create_app

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "launch_jarvis_v2.sh"


@pytest.fixture()
def secured_app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("JARVIS_API_TOKEN", "change-me-local-token")
    monkeypatch.delenv("JARVIS_ALLOW_INSECURE", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    state = JarvisCoreState(
        data_dir=tmp_path / "data",
        config_dir=ROOT / "config",
        legacy_profile_path=tmp_path / "missing.json",
    )
    return create_app(state)


def test_les_routes_api_refusent_sans_jeton(secured_app) -> None:
    with TestClient(secured_app) as client:
        assert client.get("/api/health").status_code == 200  # sonde publique
        assert client.get("/api/messages").status_code == 401
        assert client.get("/api/models").status_code == 401


def test_le_jeton_fonctionne_en_bearer_et_en_parametre(secured_app) -> None:
    token = "change-me-local-token"
    with TestClient(secured_app) as client:
        assert (
            client.get("/api/messages", headers={"Authorization": f"Bearer {token}"}).status_code
            == 200
        )
        # ?token= est le mecanisme utilise par le lanceur pour amorcer le stockage navigateur.
        assert client.get(f"/api/models?token={token}").status_code == 200
        # L'interface elle-meme ne doit jamais etre bloquee par le jeton : c'est elle qui le
        # demande. 404 seulement si web/dist n'a pas ete compile sur la machine de test.
        assert client.get("/").status_code != 401


def test_le_lanceur_transmet_le_jeton_et_ne_desactive_pas_l_auth() -> None:
    script = LAUNCHER.read_text(encoding="utf-8")
    assert 'JARVIS_ALLOW_INSECURE="${JARVIS_ALLOW_INSECURE:-1}"' not in script
    assert "?token=${TOKEN}" in script
    assert "JARVIS_API_TOKEN" in script
