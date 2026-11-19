"""Verifie que toute dependance tierce importee est declaree dans le pyproject du paquet.

Un installer propre (``uv sync`` / ``pip install ./packages/core``) ne resout que les
dependances declarees : un import non declare casse le demarrage du service sur une
machine neuve, sans que rien ne le signale en developpement (le venv de dev a souvent
la dependance par effet de bord).
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PACKAGE_DIRS: dict[str, Path] = {
    "jarvis-shared": ROOT / "packages" / "shared",
    "jarvis-core": ROOT / "packages" / "core",
    "jarvis-desktop": ROOT / "packages" / "desktop",
}

# Module importe -> nom de distribution PyPI.
IMPORT_TO_DIST: dict[str, str] = {
    "cv2": "opencv-python",
    "dotenv": "python-dotenv",
    "faster_whisper": "faster-whisper",
    "google": "google-genai",
    "huggingface_hub": "huggingface-hub",
    "PIL": "pillow",
    "pystray": "pystray",
    "pyperclip": "pyperclip",
    "rapidocr_onnxruntime": "rapidocr-onnxruntime",
    "sqlite_vec": "sqlite-vec",
    "win10toast_persist": "win10toast-persist",
    "yaml": "pyyaml",
}

# Fournis par le systeme d'exploitation ou strictement optionnels a l'execution :
# leur absence ne doit jamais empecher le service de demarrer.
SYSTEM_PROVIDED = {"gi", "sqlcipher3"}

LOCAL_MODULES = {"jarvis_shared", "jarvis_core", "jarvis_desktop", "evals", "tests"}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _declared_distributions(package_dir: Path) -> set[str]:
    data = tomllib.loads((package_dir / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project") or {}
    raw: list[str] = list(project.get("dependencies") or [])
    for group in (project.get("optional-dependencies") or {}).values():
        raw.extend(group)
    declared = set()
    for spec in raw:
        name = re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0]
        if name:
            declared.add(_normalize(name))
    return declared


def _imported_roots(package_dir: Path) -> set[str]:
    roots: set[str] = set()
    for py_file in sorted(package_dir.rglob("*.py")):
        if "build" in py_file.parts or "node_modules" in py_file.parts:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue
                roots.add(node.module.split(".")[0])
    return roots


def test_toutes_les_dependances_tierces_sont_declarees() -> None:
    stdlib = set(sys.stdlib_module_names)
    manquantes: dict[str, list[str]] = {}

    for distribution, package_dir in PACKAGE_DIRS.items():
        declared = _declared_distributions(package_dir)
        for root in sorted(_imported_roots(package_dir)):
            if root in stdlib or root in LOCAL_MODULES or root in SYSTEM_PROVIDED:
                continue
            needed = _normalize(IMPORT_TO_DIST.get(root, root))
            if needed not in declared:
                manquantes.setdefault(distribution, []).append(f"{root} -> {needed}")

    assert not manquantes, (
        "Imports tiers non declares dans le pyproject du paquet (l'installation propre "
        f"echouera) : {manquantes}"
    )


def test_aucun_artefact_de_build_versionne() -> None:
    """Un dossier build/ ou un egg-info commite duplique le source dans le depot."""
    tracked = subprocess.run(
        ["git", "ls-files", "packages"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    residus = [path for path in tracked if "/build/" in path or ".egg-info/" in path]
    assert not residus, f"Artefacts de build versionnes : {residus[:5]}"


def test_racine_de_l_app_surchargeable_pour_le_binaire(tmp_path, monkeypatch) -> None:
    """Le binaire PyInstaller doit ecrire .env, config/ et donnees a cote de l'exe."""
    from jarvis_core.api.server import JarvisCoreState

    monkeypatch.setenv("JARVIS_APP_ROOT", str(tmp_path))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("JARVIS_CONFIG_DIR", raising=False)
    state = JarvisCoreState(legacy_profile_path=tmp_path / "missing.json")

    assert state.base_root == tmp_path.resolve()
    assert state.env_file == tmp_path.resolve() / ".env"
    assert state.config_dir == tmp_path.resolve() / "config"
    assert state.data_dir == tmp_path / "data"
