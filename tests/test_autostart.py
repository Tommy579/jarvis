from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from omi_core.api.server import create_app
from omi_core.autostart import get_autostart_status, set_autostart_enabled


def test_autostart_file_lifecycle(tmp_path: Path) -> None:
    autostart_dir = tmp_path / "autostart"
    repo_root = tmp_path / "omi"
    (repo_root / "scripts").mkdir(parents=True)
    (repo_root / "scripts" / "launch_omi_v2.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    initial = get_autostart_status(autostart_dir=autostart_dir, repo_root=repo_root)
    assert initial.supported is True
    assert initial.enabled is False

    enabled_status = set_autostart_enabled(
        True, autostart_dir=autostart_dir, repo_root=repo_root
    )
    assert enabled_status.enabled is True
    desktop_file = autostart_dir / "OmiAssistant.desktop"
    assert desktop_file.is_file()
    content = desktop_file.read_text(encoding="utf-8")
    assert "Hidden=false" in content
    assert "X-GNOME-Autostart-enabled=true" in content
    assert f"Exec={repo_root / 'scripts' / 'launch_omi_v2.sh'}" in content

    read_back = get_autostart_status(autostart_dir=autostart_dir, repo_root=repo_root)
    assert read_back.enabled is True

    disabled_status = set_autostart_enabled(
        False, autostart_dir=autostart_dir, repo_root=repo_root
    )
    assert disabled_status.enabled is False
    content_disabled = desktop_file.read_text(encoding="utf-8")
    assert "Hidden=true" in content_disabled
    assert "X-GNOME-Autostart-enabled=false" in content_disabled

    read_back_disabled = get_autostart_status(autostart_dir=autostart_dir, repo_root=repo_root)
    assert read_back_disabled.enabled is False


def test_autostart_api_endpoints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMI_ALLOW_INSECURE", "1")
    monkeypatch.setenv("OMI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OMI_AUTOSTART_DIR", str(tmp_path / "autostart"))
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/system/autostart")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        resp_on = client.put("/api/system/autostart", json={"enabled": True})
        assert resp_on.status_code == 200
        assert resp_on.json()["enabled"] is True

        resp_off = client.put("/api/system/autostart", json={"enabled": False})
        assert resp_off.status_code == 200
        assert resp_off.json()["enabled"] is False
