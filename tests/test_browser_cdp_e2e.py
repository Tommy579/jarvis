"""Test bout-en-bout du pilotage navigateur sur un vrai Chromium via CDP.

Volontairement desactive par defaut (CI sans navigateur) : lancer
    JARVIS_BROWSER_E2E=1 pytest tests/test_browser_cdp_e2e.py -q
ou `make test-browser`.

Il couvre ce que les tests mockes ne peuvent pas : references versionnees sur un vrai
arbre d'accessibilite, relecture apres saisie, upload de fichier, historique et fermeture
d'onglet.
"""

from __future__ import annotations

import asyncio
import http.server
import os
import shutil
import socket
import socketserver
import subprocess
import threading
import time
from pathlib import Path

import pytest
from jarvis_core.tools.browser import (
    BrowserClickArgs,
    BrowserCloseTabArgs,
    BrowserFillArgs,
    BrowserHistoryArgs,
    BrowserOpenTabArgs,
    BrowserSelectArgs,
    BrowserSnapshotArgs,
    BrowserStatusArgs,
    BrowserToolkit,
    BrowserUploadFileArgs,
)

E2E_ENABLED = os.environ.get("JARVIS_BROWSER_E2E") == "1"
BROWSER_BIN = next(
    (
        p
        for p in (
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
        )
        if p
    ),
    None,
)

pytestmark = pytest.mark.skipif(
    not E2E_ENABLED or BROWSER_BIN is None,
    reason="JARVIS_BROWSER_E2E=1 et un binaire Chromium requis",
)

PAGE_ONE = """<!doctype html><html><head><meta charset="utf-8"><title>E2E un</title></head>
<body>
<h1>Formulaire</h1>
<label>Nom <input id="nom" type="text"></label>
<select id="sel"><option value="a">Alpha</option><option value="b">Beta</option></select>
<div id="ed" contenteditable="true" style="border:1px solid #999;min-height:40px">depart</div>
<input id="fichier" type="file" aria-label="Fichier a joindre">
<button id="go" onclick="document.getElementById('out').textContent =
  'OK|' + document.getElementById('nom').value +
  '|' + document.getElementById('sel').value +
  '|' + document.getElementById('ed').innerText.trim() +
  '|' + (document.getElementById('fichier').files[0] || {}).name">Valider</button>
<p id="out">vide</p>
<a href="/two.html" id="lien">Aller page deux</a>
</body></html>
"""

PAGE_TWO = """<!doctype html><html><head><meta charset="utf-8"><title>E2E deux</title></head>
<body><h1>Page deux</h1><p>contenu deux</p></body></html>
"""

PAGE_LONG = (
    """<!doctype html><html><head><meta charset="utf-8"><title>E2E long</title></head><body>
<h1>Liste longue</h1><ul>
"""
    + "".join(
        f"<li><button>Entree numero {i} avec un libelle volontairement long pour depasser "
        f"le budget du snapshot</button></li>"
        for i in range(1, 41)
    )
    + "</ul></body></html>"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("site")
    (root / "one.html").write_text(PAGE_ONE, encoding="utf-8")
    (root / "two.html").write_text(PAGE_TWO, encoding="utf-8")
    (root / "long.html").write_text(PAGE_LONG, encoding="utf-8")
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(root), **kw
    )
    socketserver.TCPServer.allow_reuse_address = True
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)

    def _serve() -> None:
        httpd.serve_forever(poll_interval=0.1)

    threading.Thread(target=_serve, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(scope="module")
def cdp_url(tmp_path_factory: pytest.TempPathFactory):
    port = _free_port()
    profile = tmp_path_factory.mktemp("chrome-profile")
    proc = subprocess.Popen(
        [
            str(BROWSER_BIN),
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            import httpx

            if httpx.get(f"{url}/json/version", timeout=1.0).status_code == 200:
                break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.skip("Chromium n'a pas expose son port CDP en 20 s")

    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.asyncio
async def test_real_browser_full_workflow(cdp_url: str, site: str, tmp_path: Path):
    upload_file = tmp_path / "piece-jointe.txt"
    upload_file.write_text("contenu de test", encoding="utf-8")

    toolkit = BrowserToolkit(cdp_url=cdp_url, timeout_seconds=15.0)

    status = await toolkit.browser_status(BrowserStatusArgs())
    assert status["connected"] is True

    opened = await toolkit.browser_open_tab(BrowserOpenTabArgs(url=f"{site}/one.html"))
    assert opened["ok"] is True
    tab_id = opened["tab_id"]

    first = await toolkit.browser_snapshot(BrowserSnapshotArgs(tab_id=tab_id))
    assert first["ok"] is True
    assert first["generation"] == 1
    assert "@1]" in first["snapshot"]

    by_role, by_name = _refs(first["snapshot"])
    assert "textbox" in by_role, first["snapshot"]
    assert "editable_text" in by_role, first["snapshot"]
    assert "combobox" in by_role, first["snapshot"]
    assert "Fichier a joindre" in by_name, first["snapshot"]

    filled = await toolkit.browser_fill(
        BrowserFillArgs(ref=by_role["textbox"], value="Thomas", tab_id=tab_id)
    )
    assert filled["verified"] is True, filled
    assert filled["actual_value"] == "Thomas"

    editable = await toolkit.browser_fill(
        BrowserFillArgs(ref=by_role["editable_text"], value="bonjour", tab_id=tab_id)
    )
    assert editable["verified"] is True, editable

    selected = await toolkit.browser_select(
        BrowserSelectArgs(ref=by_role["combobox"], value="Beta", tab_id=tab_id)
    )
    assert selected["ok"] is True and selected["selected"] == "b"

    uploaded = await toolkit.browser_upload_file(
        BrowserUploadFileArgs(
            ref=by_name["Fichier a joindre"], file_paths=[str(upload_file)], tab_id=tab_id
        )
    )
    assert uploaded["ok"] is True, uploaded
    assert uploaded["verified"] is True, uploaded

    clicked = await toolkit.browser_click(BrowserClickArgs(ref=by_name["Valider"], tab_id=tab_id))
    assert clicked["ok"] is True

    after = await toolkit.browser_snapshot(BrowserSnapshotArgs(tab_id=tab_id))
    assert after["generation"] == 2
    assert "OK|Thomas|b|bonjour|piece-jointe.txt" in after["snapshot"], after["snapshot"]

    # Une reference de la generation precedente doit etre refusee, pas rejouee a l'aveugle.
    with pytest.raises(RuntimeError):
        toolkit._lookup_backend_id(tab_id, by_name["Valider"])
    _, fresh = _refs(after["snapshot"])
    assert toolkit._lookup_backend_id(tab_id, fresh["Valider"])

    # Cliquer un lien puis revenir en arriere.
    link = fresh.get("Aller page deux")
    assert link is not None, after["snapshot"]
    await toolkit.browser_click(BrowserClickArgs(ref=link, tab_id=tab_id))
    moved = await toolkit._fetch_tabs()
    current = next(t for t in moved if t["id"] == tab_id)
    assert current["url"].endswith("/two.html"), current["url"]

    back = await toolkit.browser_history(BrowserHistoryArgs(direction="back", tab_id=tab_id))
    assert back["ok"] is True
    await asyncio.sleep(0.5)
    moved = await toolkit._fetch_tabs()
    current = next(t for t in moved if t["id"] == tab_id)
    assert current["url"].endswith("/one.html"), current["url"]

    closed = await toolkit.browser_close_tab(BrowserCloseTabArgs(tab_id=tab_id))
    assert closed["ok"] is True
    remaining = await toolkit._fetch_tabs()
    assert all(t["id"] != tab_id for t in remaining)

    # Pagination sur une vraie page longue : tout l'arbre doit etre lisible par pages,
    # sans changer de generation ni perdre les refs deja vues.
    long_tab = (await toolkit.browser_open_tab(BrowserOpenTabArgs(url=f"{site}/long.html")))[
        "tab_id"
    ]
    offset = 0
    pages_text: list[str] = []
    generations: set[int] = set()
    first_page_ref = None
    for _ in range(20):
        page = await toolkit.browser_snapshot(
            BrowserSnapshotArgs(tab_id=long_tab, max_chars=1000, offset=offset)
        )
        assert page["ok"] is True
        pages_text.append(page["snapshot"])
        generations.add(page["generation"])
        if first_page_ref is None:
            refs_seen = _refs(page["snapshot"])
            first_page_ref = next(iter(refs_seen[0].values()), None)
        if not page["next_offset"]:
            break
        offset = page["next_offset"]
    joined = "\n".join(pages_text)
    assert len(pages_text) >= 2, joined
    assert len(generations) == 1, generations
    assert "Entree numero 1 " in joined
    assert "Entree numero 40 " in joined, joined[-400:]
    assert first_page_ref is not None
    assert toolkit._lookup_backend_id(long_tab, first_page_ref) > 0


def _refs(snapshot: str) -> tuple[dict[str, str], dict[str, str]]:
    """Retourne (role -> ref, nom accessible -> ref) depuis le texte du snapshot."""
    import re

    by_role: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for line in snapshot.splitlines():
        match = re.match(r'\s*\[(e\d+@\d+)\]\s+(\S+)(?:\s+"([^"]*)")?', line)
        if not match:
            continue
        ref = match.group(1)
        role = match.group(2)
        name = match.group(3) or ""
        by_role.setdefault(role, ref)
        if name:
            by_name.setdefault(name, ref)
    return by_role, by_name
