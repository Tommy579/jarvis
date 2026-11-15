"""Bac a sable Podman sans reseau (--network=none) pour l'outil run_python."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class PodmanSandbox:
    """Execute du code Python d'analyse dans un conteneur podman ephemere isole."""

    def __init__(
        self,
        image: str = "python:3.12-slim",
        memory_limit: str = "256m",
        cpus: str = "1.0",
        timeout_seconds: int = 15,
    ):
        self.image = image
        self.memory_limit = memory_limit
        self.cpus = cpus
        self.timeout_seconds = timeout_seconds

    def build_podman_command(self, script_path: Path, workspace_dir: Path) -> list[str]:
        return [
            "podman",
            "run",
            "--rm",
            "--network=none",
            f"--memory={self.memory_limit}",
            f"--cpus={self.cpus}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
            "-v",
            f"{workspace_dir}:/workspace:ro",
            "-v",
            f"{script_path}:/script.py:ro",
            "--workdir=/workspace",
            self.image,
            "python3",
            "-I",
            "/script.py",
        ]

    def run_code(self, code: str, workspace_dir: Path | None = None) -> dict[str, Any]:
        if not shutil.which("podman"):
            return {
                "ok": False,
                "error": "Podman n'est pas installe sur le systeme. Execution refusee hors bac a sable.",
            }

        with tempfile.TemporaryDirectory(prefix="omi_sandbox_") as tmpdir:
            tmp_path = Path(tmpdir)
            script_file = tmp_path / "script.py"
            script_file.write_text(code, encoding="utf-8")
            work_dir = workspace_dir or tmp_path
            cmd = self.build_podman_command(script_file, work_dir)
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
                return {
                    "ok": proc.returncode == 0,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[:10000],
                    "stderr": proc.stderr[:10000],
                }
            except subprocess.TimeoutExpired:
                return {
                    "ok": False,
                    "error": f"Delai d'execution depasse ({self.timeout_seconds}s) dans le bac a sable.",
                }
            except Exception as e:
                return {"ok": False, "error": str(e)}
