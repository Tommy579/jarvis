"""Tests statiques de durcissement : systemd, Docker, lanceur, installeur, configuration."""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _unit(name: str) -> dict[str, list[str]]:
    """Lit une unite systemd en conservant les cles repetees."""
    values: dict[str, list[str]] = {}
    section = ""
    for line in (ROOT / "deploy" / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            continue
        key, _, value = line.partition("=")
        if section == "Service":
            values.setdefault(key, []).append(value)
    return values


# --- systemd ---------------------------------------------------------------------


def test_core_unit_never_runs_as_root_and_is_hardened() -> None:
    svc = _unit("jarvis-core.service")
    assert svc.get("User") and svc["User"][0] not in ("", "root")
    expected = {
        "UMask": "0077",
        "NoNewPrivileges": "true",
        "PrivateTmp": "true",
        "ProtectSystem": "strict",
        "ProtectHome": "true",
        "RestrictSUIDSGID": "true",
        "CapabilityBoundingSet": "",
    }
    for key, value in expected.items():
        assert svc.get(key) == [value], key
    assert svc["ReadWritePaths"] == ["/opt/jarvis"]
    families = svc["RestrictAddressFamilies"][0].split()
    assert {"AF_UNIX", "AF_INET", "AF_INET6"} <= set(families)
    # Le micro et la camera doivent rester accessibles.
    assert "PrivateDevices" not in svc
    assert "--host 127.0.0.1" in svc["ExecStart"][0]


def test_desktop_unit_has_user_compatible_hardening() -> None:
    svc = _unit("jarvis-desktop.service")
    assert svc.get("UMask") == ["0077"]
    assert svc.get("NoNewPrivileges") == ["true"]
    # Une unite utilisateur qui lit la session ne doit pas masquer /home.
    assert "ProtectHome" not in svc
    assert "ProtectSystem" not in svc


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze absent")
def test_core_unit_exposure_score_is_ok() -> None:
    res = subprocess.run(
        [
            "systemd-analyze",
            "security",
            "--offline=yes",
            str(ROOT / "deploy" / "jarvis-core.service"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    match = re.search(r"exposure level for \S+: ([0-9.]+)", res.stdout)
    if not match:
        pytest.skip("sortie systemd-analyze non reconnue")
    assert float(match.group(1)) <= 5.0


# --- Docker ----------------------------------------------------------------------


def test_dockerignore_excludes_nested_secrets_and_data() -> None:
    lines = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for pattern in (
        "**/.env",
        "**/.env.*",
        "**/*.db",
        "**/jarvis_profile.json",
        "jarvis-data/",
        "**/*.pem",
        "**/*.key",
        "**/secrets.enc",
    ):
        assert pattern in lines, pattern
    assert "!.env.example" in lines


def test_dockerfile_runs_unprivileged_with_private_umask() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    users = re.findall(r"^USER\s+(\S+)", text, flags=re.M)
    assert users and users[-1] not in ("root", "0")
    assert "umask 077" in text
    assert "chmod 700 /data" in text
    # Aucun secret ni donnee personnelle copie explicitement dans l'image.
    for forbidden in (".env", "jarvis-data", "jarvis_profile.json", "*.db"):
        assert not re.search(rf"^COPY[^\n]*{re.escape(forbidden)}", text, flags=re.M), forbidden
    # Le healthcheck interroge le port reellement servi.
    assert "127.0.0.1:8765/api/health" in text
    assert "--port 8765" in text


def test_compose_drops_capabilities_and_publishes_on_loopback() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"cap_drop:\s*\n\s*-\s*ALL", text)
    assert "no-new-privileges:true" in text
    assert '"127.0.0.1:8765:8765"' in text
    assert ":/data" in text


# --- Lanceur et installeur --------------------------------------------------------


def test_launcher_restricts_permissions_and_does_not_log_token() -> None:
    text = (ROOT / "scripts" / "launch_jarvis_v2.sh").read_text(encoding="utf-8")
    assert re.search(r"^umask 077$", text, flags=re.M)
    assert 'chmod 700 "$STATE_DIR"' in text
    assert "flock" in text
    # uvicorn ne doit pas heriter du verrou, sinon le lancement suivant bloque.
    assert re.search(r"jarvis-core\.log\" 2>&1 9>&- &", text)
    # Le jeton n'apparait en clair que sur un terminal interactif.
    backend_only = text.split('"--backend-only"', 1)[1].split("exit 0", 1)[0]
    assert "[ -t 1 ]" in backend_only


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="bash requis")
def test_shell_scripts_parse() -> None:
    for script in ("scripts/launch_jarvis_v2.sh", "install_linux.sh"):
        subprocess.run(["bash", "-n", str(ROOT / script)], check=True, timeout=10)


def test_installer_uses_frozen_lock_and_private_env() -> None:
    text = (ROOT / "install_linux.sh").read_text(encoding="utf-8")
    assert "uv sync --frozen" in text
    assert not re.search(r"uv sync(?! --frozen)", text)
    assert "umask 077" in text
    assert not re.search(r"curl[^\n]*\|\s*(?:ba)?sh", text)
    # La cle n'est jamais injectee telle quelle dans sed sans controle des caracteres.
    assert "*[!A-Za-z0-9_.-]*" in text


# --- Configuration ---------------------------------------------------------------


def test_env_example_has_no_usable_placeholder_secrets() -> None:
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    parser.read_string("[env]\n" + content)
    env = parser["env"]
    for key in ("JARVIS_API_TOKEN", "ACTION_TOKEN_SECRET"):
        assert env.get(key, "") == "", f"{key} doit etre vide dans .env.example"
    assert env.get("JARVIS_ALLOW_INSECURE") == "0"
    assert env.get("JARVIS_HOST") == "127.0.0.1"


def test_gitignore_covers_sqlite_side_files_and_env_variants() -> None:
    lines = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    for pattern in (".env", ".env.*", "!.env.example", "*.db-wal", "*.db-shm", "jarvis-data/"):
        assert pattern in lines, pattern


def test_mcp_servers_are_disabled_and_pinned() -> None:
    import yaml

    data = yaml.safe_load((ROOT / "config" / "mcp.yaml").read_text(encoding="utf-8"))
    for name, server in (data.get("servers") or {}).items():
        args = " ".join(str(a) for a in server.get("args", []))
        packages = [a for a in server.get("args", []) if str(a).startswith("@")]
        for pkg in packages:
            assert re.search(r"@\d[\w.\-]*$", str(pkg)), f"{name} : version non epinglee ({pkg})"
        assert "@latest" not in args
        assert server.get("enabled") is False, f"{name} actif par defaut"
