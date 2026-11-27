"""Tests unitaires et d'integration pour l'autonomie, le navigateur CDP, AT-SPI et MCP."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from jarvis_core.agent.autonomous import AutonomousTaskManager
from jarvis_core.agent.loop import AgentLoop
from jarvis_core.api.server import JarvisCoreState, create_app
from jarvis_core.llm.base import LLMProvider
from jarvis_core.llm.router import LLMRouter
from jarvis_core.mcp.client import MCPClientManager, MCPServerConfig
from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.tools.approvals import ApprovalManager
from jarvis_core.tools.autonomy import AutonomyPolicy
from jarvis_core.tools.browser import BrowserToolkit, format_ax_tree
from jarvis_core.tools.desktop_a11y import DesktopA11yToolkit, _is_blacklisted_app
from jarvis_core.tools.registry import ToolRegistry, ToolSpec
from jarvis_shared.models import (
    Message,
    ModelCapabilities,
    RiskLevel,
    StreamDelta,
    ToolCall,
    Usage,
)
from jarvis_shared.rest_models import AutonomyPolicyUpdate
from pydantic import BaseModel


class _UrlArgs(BaseModel):
    url: str = ""
    command: str = ""
    ref: str = ""


class _FakeSendArgs(BaseModel):
    to: str = "a@b.com"


class _FakeTelegramSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, int]] = []

    async def send(self, message: str, urgency: int = 1, inline_keyboard=None):
        self.sent.append((message, urgency))
        return {"ok": True}


class _ScriptedAutonomousProvider(LLMProvider):
    """Simule un LLM qui parcourt 2 onglets QCM (>8 iterations) puis rend son bilan."""

    name = "openrouter"

    def __init__(self) -> None:
        self.step = 0

    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        return {}

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools=None,
        fallback_models=None,
        temperature: float = 0.2,
    ) -> StreamDelta:
        self.step += 1
        # 10 etapes d'outils (> MAX_ITERATIONS=8) pour prouver la boucle autonome etendue
        script: dict[int, list[ToolCall]] = {
            1: [ToolCall(id="c1", name="browser_list_tabs", arguments={})],
            2: [ToolCall(id="c2", name="browser_snapshot", arguments={"tab_id": "tab-1"})],
            3: [
                ToolCall(id="c3", name="browser_click", arguments={"ref": "e1", "tab_id": "tab-1"})
            ],
            4: [
                ToolCall(id="c4", name="browser_click", arguments={"ref": "e2", "tab_id": "tab-1"})
            ],
            5: [ToolCall(id="c5", name="browser_snapshot", arguments={"tab_id": "tab-1"})],
            6: [ToolCall(id="c6", name="browser_snapshot", arguments={"tab_id": "tab-2"})],
            7: [
                ToolCall(id="c7", name="browser_click", arguments={"ref": "e1", "tab_id": "tab-2"})
            ],
            8: [
                ToolCall(id="c8", name="browser_click", arguments={"ref": "e2", "tab_id": "tab-2"})
            ],
            9: [ToolCall(id="c9", name="browser_snapshot", arguments={"tab_id": "tab-2"})],
        }
        calls = script.get(self.step)
        usage = Usage(model=model, provider=self.name, tokens_in=100, tokens_out=40, cost_usd=0.002)
        if calls:
            return StreamDelta(text="", tool_calls=calls, usage=usage)
        return StreamDelta(
            text="Les 2 QCM ouverts (tab-1 et tab-2) ont ete completes et soumis avec succes.",
            usage=usage,
        )

    async def stream(self, model, messages, tools=None, fallback_models=None, temperature=0.2):
        yield await self.complete(model, messages, tools, fallback_models, temperature)


def test_format_ax_tree_extracts_compact_refs():
    nodes = [
        {
            "nodeId": "1",
            "role": {"value": "RootWebArea"},
            "name": {"value": "QCM Python"},
            "childIds": ["2", "3", "4"],
        },
        {
            "nodeId": "2",
            "role": {"value": "heading"},
            "name": {"value": "Question 1"},
            "childIds": [],
        },
        {
            "nodeId": "3",
            "role": {"value": "radio"},
            "name": {"value": "42"},
            "backendDOMNodeId": 101,
            "properties": [{"name": "checked", "value": {"value": False}}],
            "childIds": ["3a"],
        },
        {"nodeId": "3a", "role": {"value": "StaticText"}, "name": {"value": "42"}, "childIds": []},
        {
            "nodeId": "4",
            "role": {"value": "button"},
            "name": {"value": "Soumettre"},
            "backendDOMNodeId": 102,
            "childIds": [],
        },
    ]
    text, ref_map, next_offset = format_ax_tree(nodes)
    assert next_offset == 0
    assert '[e1] radio "42"' in text
    assert '[e2] button "Soumettre"' in text
    assert 'heading "Question 1"' in text
    assert ref_map == {"e1": 101, "e2": 102}
    # StaticText redondant sous le radio ne doit pas dupliquer la ligne
    assert text.count('"42"') == 1


@pytest.mark.asyncio
async def test_autonomy_policy_guards_domains_and_emergency_stop():
    policy = AutonomyPolicy(
        mode="autonomous",
        allowed_domains=["moodle.univ.fr"],
        blocked_domains=["paypal.com"],
    )
    approvals = ApprovalManager(ttl_seconds=1.0)
    registry = ToolRegistry(approval_manager=approvals, autonomy_policy=policy)

    registry.register(
        ToolSpec(
            name="browser_navigate",
            description="Navigue",
            handler=lambda url="", **kwargs: {"ok": True, "url": url},
            args_model=_UrlArgs,
            risk=RiskLevel.REVERSIBLE_WRITE,
            is_interactive_ui=True,
        )
    )
    registry.register(
        ToolSpec(
            name="browser_click",
            description="Clic",
            handler=lambda ref="", **kwargs: {"ok": True, "ref": ref},
            args_model=_UrlArgs,
            risk=RiskLevel.SENSITIVE,
            is_interactive_ui=True,
        )
    )
    registry.register(
        ToolSpec(
            name="run_command",
            description="Shell",
            handler=lambda command="", **kwargs: {"ok": True, "command": command},
            args_model=_UrlArgs,
            risk=RiskLevel.REVERSIBLE_WRITE,
        )
    )
    registry.register(
        ToolSpec(
            name="mail_send",
            description="Envoi mail",
            handler=lambda to="", **kwargs: {"ok": True},
            args_model=_FakeSendArgs,
            risk=RiskLevel.SENSITIVE,
        )
    )
    registry.register(
        ToolSpec(
            name="dangerous_op",
            description="Niveau 3",
            handler=lambda command="", **kwargs: {"ok": True},
            args_model=_UrlArgs,
            risk=RiskLevel.DANGEROUS,
        )
    )

    # Domaine autorise ok, domaine bloque ou hors liste blanche refuse
    ok_nav = await registry.execute(
        ToolCall(name="browser_navigate", arguments={"url": "https://quiz.moodle.univ.fr/qcm/1"})
    )
    assert not ok_nav.is_error

    blocked_dom = await registry.execute(
        ToolCall(name="browser_navigate", arguments={"url": "https://evil.com/phish"})
    )
    assert blocked_dom.is_error
    assert blocked_dom.output.get("blocked_by") == "domain_policy"

    blocked_js = await registry.execute(
        ToolCall(name="browser_navigate", arguments={"url": "javascript:alert(1)"})
    )
    assert blocked_js.is_error

    # En contexte <untrusted>, browser_click (UI niveau 2) est auto-approuve en mode autonome,
    # alors que run_command, mail_send et niveau 3 restent strictement bloques !
    click_res = await registry.execute(
        ToolCall(name="browser_click", arguments={"ref": "e1"}),
        has_untrusted_context=True,
        autonomous_run=True,
    )
    assert not click_res.is_error

    shell_res = await registry.execute(
        ToolCall(name="run_command", arguments={"command": "whoami"}),
        has_untrusted_context=True,
        autonomous_run=True,
    )
    assert shell_res.is_error
    assert shell_res.output.get("blocked_by") == "untrusted_context_guard"

    mail_res = await registry.execute(
        ToolCall(name="mail_send", arguments={"to": "attacker@evil.com"}),
        has_untrusted_context=True,
        autonomous_run=True,
    )
    assert mail_res.is_error
    assert mail_res.output.get("blocked_by") == "untrusted_context_guard"

    lvl3_res = await registry.execute(
        ToolCall(name="dangerous_op", arguments={"command": "rm -rf /"}),
        has_untrusted_context=True,
        autonomous_run=True,
    )
    assert lvl3_res.is_error

    # Kill switch bloque immediatement toute action
    policy.update(AutonomyPolicyUpdate(emergency_stop=True))
    stopped_res = await registry.execute(
        ToolCall(name="browser_click", arguments={"ref": "e1"}),
        autonomous_run=True,
    )
    assert stopped_res.is_error
    assert stopped_res.output.get("blocked_by") == "emergency_stop"


@pytest.mark.asyncio
async def test_mcp_stdio_real_handshake_and_tool_call():
    with tempfile.TemporaryDirectory() as tmpdir:
        srv_script = Path(tmpdir) / "mock_mcp.py"
        srv_script.write_text(
            "import json, sys\n"
            "for line in sys.stdin:\n"
            "    line = line.strip()\n"
            "    if not line:\n"
            "        continue\n"
            "    msg = json.loads(line)\n"
            "    mid = msg.get('id')\n"
            "    method = msg.get('method')\n"
            "    if mid is None:\n"
            "        continue\n"
            "    if method == 'initialize':\n"
            "        res = {'protocolVersion': '2024-11-05', 'capabilities': {}, 'serverInfo': {'name': 'mock'}}\n"
            "    elif method == 'tools/list':\n"
            "        res = {'tools': [{'name': 'ping', 'description': 'Ping test', 'inputSchema': {'properties': {'msg': {'type': 'string'}}}}]}\n"
            "    elif method == 'tools/call':\n"
            "        args = msg.get('params', {}).get('arguments', {})\n"
            "        res = {'content': [{'type': 'text', 'text': f\"pong:{args.get('msg', '')}\"}]}\n"
            "    else:\n"
            "        res = {}\n"
            "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': mid, 'result': res}) + '\\n')\n"
            "    sys.stdout.flush()\n",
            encoding="utf-8",
        )
        registry = ToolRegistry()
        mgr = MCPClientManager(registry)
        mgr.register_server(
            MCPServerConfig(
                name="playwright",
                transport="stdio",
                command=sys.executable,
                args=[str(srv_script)],
                enabled=True,
                default_risk=RiskLevel.SENSITIVE,
            )
        )
        try:
            imported = await mgr.connect_server("playwright")
            assert imported == ["mcp_playwright_ping"]
            spec = registry.get("mcp_playwright_ping")
            assert spec is not None
            assert spec.is_interactive_ui is True

            res = await registry.execute(
                ToolCall(name="mcp_playwright_ping", arguments={"msg": "qcm"}),
                pre_approved=True,
            )
            assert not res.is_error
            assert "pong:qcm" in str(res.output)
        finally:
            await mgr.disconnect_all()


@pytest.mark.asyncio
async def test_autonomous_qcm_workflow_exceeding_8_steps_and_notifying():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = MemoryDatabase(Path(tmpdir) / "jarvis.db")
        assert db.schema_version >= 4

        policy = AutonomyPolicy(mode="autonomous")
        registry = ToolRegistry(autonomy_policy=policy)

        tabs = [
            {"tab_id": "tab-1", "title": "QCM 1", "url": "https://quiz.local/1"},
            {"tab_id": "tab-2", "title": "QCM 2", "url": "https://quiz.local/2"},
        ]
        submitted: list[str] = []

        class _NoArgs(BaseModel):
            pass

        class _TabArgs(BaseModel):
            tab_id: str | None = None

        class _ClickArgs(BaseModel):
            ref: str
            tab_id: str | None = None

        registry.register(
            ToolSpec(
                name="browser_list_tabs",
                description="Tabs",
                handler=lambda: {"ok": True, "tabs": tabs},
                args_model=_NoArgs,
                risk=RiskLevel.READ_ONLY,
                returns_untrusted=True,
            )
        )
        registry.register(
            ToolSpec(
                name="browser_snapshot",
                description="Snapshot",
                handler=lambda tab_id=None: {
                    "ok": True,
                    "tab_id": tab_id,
                    "snapshot": (
                        f"Status: {'Soumis' if tab_id in submitted else 'En cours'}\n"
                        '[e1] radio "Reponse A"\n[e2] button "Valider"'
                    ),
                },
                args_model=_TabArgs,
                risk=RiskLevel.READ_ONLY,
                returns_untrusted=True,
            )
        )
        registry.register(
            ToolSpec(
                name="browser_click",
                description="Click",
                handler=lambda ref="", tab_id=None: (
                    submitted.append(tab_id) if ref == "e2" and tab_id else None,
                    {"ok": True, "clicked_ref": ref, "tab_id": tab_id},
                )[1],
                args_model=_ClickArgs,
                risk=RiskLevel.SENSITIVE,
                is_interactive_ui=True,
            )
        )

        provider = _ScriptedAutonomousProvider()
        router = LLMRouter(providers={"openrouter": provider})
        tg = _FakeTelegramSender()
        alert_q: asyncio.Queue = asyncio.Queue()

        mgr = AutonomousTaskManager(
            router=router,
            tool_registry=registry,
            database=db,
            policy=policy,
            channels={"telegram": tg},
            alert_queue=alert_q,
        )
        rec = mgr.start_task(
            goal="Va sur tous les QCM ouverts, reponds, soumets et notifie-moi.",
            max_steps=20,
            notify_on_finish=True,
        )
        # Attendre la fin de la tache en arriere-plan
        for _ in range(100):
            current = mgr.get_task(rec.id)
            if current and current.status in ("completed", "failed", "cancelled"):
                break
            await asyncio.sleep(0.02)

        final_task = mgr.get_task(rec.id)
        assert final_task is not None
        assert final_task.status == "completed"
        assert final_task.iterations == 10
        assert submitted == ["tab-1", "tab-2"]
        assert "2 QCM" in final_task.result_summary
        assert len(tg.sent) == 1
        assert "Tâche autonome terminée" in tg.sent[0][0]
        assert not alert_q.empty()


@pytest.mark.asyncio
async def test_browser_toolkit_cdp_mocked_flow():
    toolkit = BrowserToolkit(cdp_url="http://127.0.0.1:9222")
    fake_tabs = [
        {
            "id": "tab-abc",
            "type": "page",
            "title": "QCM Test",
            "url": "https://moodle.univ.fr/mod/quiz",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab-abc",
        }
    ]

    class _FakeCDP:
        def __init__(self, ws_url: str, timeout: float = 10.0) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call(self, method: str, params=None):
            self.calls.append((method, params or {}))
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        {
                            "nodeId": "1",
                            "role": {"value": "radio"},
                            "name": {"value": "Option B"},
                            "backendDOMNodeId": 77,
                            "childIds": [],
                        }
                    ]
                }
            if method == "DOM.getBoxModel":
                return {"model": {"content": [10, 10, 50, 10, 50, 30, 10, 30]}}
            return {}

    with (
        patch.object(toolkit, "_fetch_tabs", AsyncMock(return_value=fake_tabs)),
        patch("jarvis_core.tools.browser._CDPSession", _FakeCDP),
    ):
        from jarvis_core.tools.browser import BrowserClickArgs, BrowserSnapshotArgs

        snap = await toolkit.browser_snapshot(BrowserSnapshotArgs(activate=False))
        assert snap["ok"] is True
        assert snap["generation"] == 1
        assert '[e1@1] radio "Option B"' in snap["snapshot"]

        click = await toolkit.browser_click(BrowserClickArgs(ref="e1"))
        assert click["ok"] is True
        assert click["method"] == "mouse"


def _make_fake_cdp(ax_nodes: list[dict], state: dict | None = None):
    """Fabrique une fausse session CDP : arbre AX fixe, DOM en memoire pour fill/upload."""
    state = state if state is not None else {}
    state.setdefault("value", "")

    class _FakeCDP:
        def __init__(self, ws_url: str, timeout: float = 10.0, **kwargs) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call(self, method: str, params=None, **kwargs):
            params = params or {}
            self.calls.append((method, params))
            if method == "Accessibility.getFullAXTree":
                return {"nodes": ax_nodes}
            if method == "DOM.getBoxModel":
                return {"model": {"content": [10, 10, 50, 10, 50, 30, 10, 30]}}
            if method == "DOM.resolveNode":
                return {"object": {"objectId": "obj-1"}}
            if method == "Runtime.callFunctionOn":
                decl = str(params.get("functionDeclaration") or "")
                if "return this.value" in decl:
                    return {"result": {"value": state["value"]}}
                if "map(x => x.name)" in decl:
                    return {"result": {"value": list(state.get("files", []))}}
                if "this.value" in decl:
                    # setter : on memorise la valeur saisie
                    args = params.get("arguments") or []
                    if args and isinstance(args[0], dict):
                        state["value"] = str(args[0].get("value", state["value"]))
                    return {"result": {"value": True}}
                return {"result": {"value": True}}
            if method == "Runtime.evaluate":
                return {"result": {"value": {"ready": "complete", "count": 3, "size": 10}}}
            if method == "Input.insertText":
                state["value"] = str(params.get("text", ""))
                return {}
            if method == "DOM.setFileInputFiles":
                state["files"] = [Path(f).name for f in params.get("files", [])]
                return {}
            if method == "Page.captureScreenshot":
                import base64

                fake_png = base64.b64encode(b"fake_png_bytes").decode("ascii")
                return {"data": fake_png}
            return {}

    return _FakeCDP


_AX_TWO_STEPS = [
    {
        "nodeId": "1",
        "role": {"value": "RootWebArea"},
        "name": {"value": "Probe"},
        "childIds": ["2", "3"],
    },
    {
        "nodeId": "2",
        "role": {"value": "textbox"},
        "name": {"value": "Nom"},
        "backendDOMNodeId": 11,
        "childIds": [],
    },
    {
        "nodeId": "3",
        "role": {"value": "button"},
        "name": {"value": "Valider"},
        "backendDOMNodeId": 12,
        "childIds": [],
    },
]


@pytest.mark.asyncio
async def test_refs_are_versioned_and_stale_refs_are_rejected():
    toolkit = BrowserToolkit(cdp_url="http://127.0.0.1:9222")
    fake_tabs = [
        {
            "id": "tab-v",
            "type": "page",
            "title": "Probe",
            "url": "https://probe.local/1",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab-v",
        }
    ]
    with (
        patch.object(toolkit, "_fetch_tabs", AsyncMock(return_value=fake_tabs)),
        patch("jarvis_core.tools.browser._CDPSession", _make_fake_cdp(_AX_TWO_STEPS)),
    ):
        from jarvis_core.tools.browser import BrowserSnapshotArgs

        first = await toolkit.browser_snapshot(BrowserSnapshotArgs())
        assert first["generation"] == 1
        assert "[e2@1] button" in first["snapshot"]

        second = await toolkit.browser_snapshot(BrowserSnapshotArgs())
        assert second["generation"] == 2
        assert "[e2@2] button" in second["snapshot"]

        # Reference de la generation precedente : refus explicite, pas de clic silencieux.
        with pytest.raises(RuntimeError) as stale:
            toolkit._lookup_backend_id("tab-v", "e2@1")
        assert "perimee" in str(stale.value)

        # Reference sans version alors que la page a ete relue : ambigue.
        with pytest.raises(RuntimeError) as ambiguous:
            toolkit._lookup_backend_id("tab-v", "e2")
        assert "ambigue" in str(ambiguous.value)

        # La reference a jour fonctionne.
        assert toolkit._lookup_backend_id("tab-v", "e2@2") == 12
        assert toolkit._lookup_backend_id("tab-v", "[e2@2]") == 12


@pytest.mark.asyncio
async def test_snapshot_respects_domain_policy_on_read():
    policy = AutonomyPolicy(blocked_domains=["probe.local"])
    toolkit = BrowserToolkit(cdp_url="http://127.0.0.1:9222", autonomy_policy=policy)
    fake_tabs = [
        {
            "id": "tab-d",
            "type": "page",
            "title": "Probe",
            "url": "https://probe.local/1",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab-d",
        }
    ]
    with (
        patch.object(toolkit, "_fetch_tabs", AsyncMock(return_value=fake_tabs)),
        patch("jarvis_core.tools.browser._CDPSession", _make_fake_cdp(_AX_TWO_STEPS)),
    ):
        from jarvis_core.tools.browser import BrowserSnapshotArgs

        snap = await toolkit.browser_snapshot(BrowserSnapshotArgs())
        assert snap["ok"] is False
        assert snap["blocked_by"] == "domain_policy"
        assert "snapshot" not in snap


@pytest.mark.asyncio
async def test_fill_reports_real_readback_not_echo():
    toolkit = BrowserToolkit(cdp_url="http://127.0.0.1:9222")
    fake_tabs = [
        {
            "id": "tab-f",
            "type": "page",
            "title": "Probe",
            "url": "https://probe.local/1",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab-f",
        }
    ]
    state: dict = {"value": ""}
    with (
        patch.object(toolkit, "_fetch_tabs", AsyncMock(return_value=fake_tabs)),
        patch("jarvis_core.tools.browser._CDPSession", _make_fake_cdp(_AX_TWO_STEPS, state)),
    ):
        from jarvis_core.tools.browser import BrowserFillArgs, BrowserSnapshotArgs

        await toolkit.browser_snapshot(BrowserSnapshotArgs())
        filled = await toolkit.browser_fill(BrowserFillArgs(ref="e1@1", value="Thomas"))
        assert filled["ok"] is True
        assert filled["verified"] is True
        assert filled["actual_value"] == "Thomas"
        assert "verified_value" not in filled


@pytest.mark.asyncio
async def test_browser_screenshot_and_challenge_detection():
    toolkit = BrowserToolkit(cdp_url="http://127.0.0.1:9222")
    fake_tabs = [
        {
            "id": "tab-challenge",
            "type": "page",
            "title": "Just a moment... Cloudflare Turnstile Challenge",
            "url": "https://protected.example.com",
            "webSocketDebuggerUrl": "ws://fake/tab-challenge",
        }
    ]
    ax_challenge = [
        {
            "nodeId": "1",
            "role": {"value": "RootWebArea"},
            "name": {"value": "Verify you are human"},
            "childIds": ["2"],
        },
        {
            "nodeId": "2",
            "role": {"value": "button"},
            "name": {"value": "Cloudflare challenge"},
            "backendDOMNodeId": 99,
            "childIds": [],
        },
    ]

    with (
        patch.object(toolkit, "_fetch_tabs", AsyncMock(return_value=fake_tabs)),
        patch("jarvis_core.tools.browser._CDPSession", _make_fake_cdp(ax_challenge)),
    ):
        from jarvis_core.tools.browser import BrowserScreenshotArgs, BrowserSnapshotArgs

        snap = await toolkit.browser_snapshot(BrowserSnapshotArgs())
        assert snap["ok"] is True
        assert snap["challenge_detected"] is True

        # Test screenshot element
        shot_elem = await toolkit.browser_screenshot(BrowserScreenshotArgs(ref="e1@1"))
        assert shot_elem["ok"] is True
        assert shot_elem["ref"] == "e1@1"
        assert shot_elem["bytes_count"] > 0
        assert Path(shot_elem["path"]).exists()
        Path(shot_elem["path"]).unlink(missing_ok=True)

        # Test screenshot full viewport
        shot_page = await toolkit.browser_screenshot(BrowserScreenshotArgs(full_page=True))
        assert shot_page["ok"] is True
        assert shot_page["full_page"] is True
        assert Path(shot_page["path"]).exists()
        Path(shot_page["path"]).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_ui_tool_on_untrusted_context_goes_to_approval_not_hard_refusal():
    """En mode supervise (defaut), lire une page ne doit plus rendre le clic impossible."""
    policy = AutonomyPolicy(mode="supervised")
    approvals = ApprovalManager(ttl_seconds=0.5)
    registry = ToolRegistry(approval_manager=approvals, autonomy_policy=policy)

    registry.register(
        ToolSpec(
            name="browser_click",
            description="Clic",
            handler=lambda ref="", **kwargs: {"ok": True, "ref": ref},
            args_model=_UrlArgs,
            risk=RiskLevel.SENSITIVE,
            is_interactive_ui=True,
        )
    )
    registry.register(
        ToolSpec(
            name="run_command",
            description="Shell",
            handler=lambda command="", **kwargs: {"ok": True},
            args_model=_UrlArgs,
            risk=RiskLevel.REVERSIBLE_WRITE,
        )
    )

    click_res = await registry.execute(
        ToolCall(name="browser_click", arguments={"ref": "e1@1"}),
        has_untrusted_context=True,
        approval_timeout=0.1,
    )
    # Le clic n'est plus refuse par le garde-fou : il attend une approbation humaine.
    assert click_res.is_error is True
    assert click_res.output.get("blocked_by") != "untrusted_context_guard"
    assert "Approbation requise" in str(click_res.output.get("error"))

    # Le shell reste strictement bloque sur contexte non fiable (tache autonome).
    shell_res = await registry.execute(
        ToolCall(name="run_command", arguments={"command": "whoami"}),
        has_untrusted_context=True,
        autonomous_run=True,
    )
    assert shell_res.is_error is True
    assert shell_res.output.get("blocked_by") == "untrusted_context_guard"


@pytest.mark.asyncio
async def test_snapshot_pagination_covers_whole_tree_with_stable_generation():
    """Un arbre trop gros doit etre lisible par pages, sans perimer les refs deja vues."""
    children = [str(i) for i in range(2, 42)]
    nodes = [
        {
            "nodeId": "1",
            "role": {"value": "RootWebArea"},
            "name": {"value": "Gros"},
            "childIds": children,
        },
    ]
    for i in range(2, 42):
        nodes.append(
            {
                "nodeId": str(i),
                "role": {"value": "button"},
                "name": {"value": f"Bouton numero {i} avec un libelle assez long"},
                "backendDOMNodeId": 1000 + i,
                "childIds": [],
            }
        )

    toolkit = BrowserToolkit(cdp_url="http://127.0.0.1:9222")
    fake_tabs = [
        {
            "id": "tab-p",
            "type": "page",
            "title": "Gros",
            "url": "https://probe.local/gros",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab-p",
        }
    ]
    with (
        patch.object(toolkit, "_fetch_tabs", AsyncMock(return_value=fake_tabs)),
        patch("jarvis_core.tools.browser._CDPSession", _make_fake_cdp(nodes)),
    ):
        from jarvis_core.tools.browser import BrowserSnapshotArgs

        seen_text: list[str] = []
        offset = 0
        pages = 0
        first_ref = None
        while True:
            snap = await toolkit.browser_snapshot(
                BrowserSnapshotArgs(tab_id="tab-p", max_chars=1000, offset=offset)
            )
            assert snap["ok"] is True
            assert snap["generation"] == 1, "la pagination ne doit pas changer la generation"
            seen_text.append(snap["snapshot"])
            if first_ref is None:
                first_ref = next(iter(toolkit._ref_maps["tab-p"]))
            pages += 1
            if not snap["next_offset"]:
                break
            offset = snap["next_offset"]
            assert pages < 20, "pagination qui boucle"

        joined = "\n".join(seen_text)
        assert pages >= 2
        for i in range(2, 42):
            assert f"Bouton numero {i} " in joined
        # Les refs de la premiere page restent utilisables apres pagination.
        assert toolkit._lookup_backend_id("tab-p", first_ref) > 0


@pytest.mark.asyncio
async def test_tool_declared_usage_is_billed_and_stripped_from_output():
    from jarvis_core.tools.registry import TOOL_USAGE_KEY

    policy = AutonomyPolicy(mode="autonomous")
    registry = ToolRegistry(autonomy_policy=policy)

    class _NoArgs(BaseModel):
        pass

    registry.register(
        ToolSpec(
            name="browser_analyze_media",
            description="Media",
            handler=lambda: {
                "ok": True,
                "analysis": "resume",
                TOOL_USAGE_KEY: {
                    "model": "gemini-test",
                    "provider": "gemini",
                    "tokens_in": 1200,
                    "tokens_out": 300,
                    "cost_usd": 0.0042,
                },
            },
            args_model=_NoArgs,
            risk=RiskLevel.READ_ONLY,
            returns_untrusted=True,
        )
    )

    res = await registry.execute(ToolCall(name="browser_analyze_media", arguments={}))
    assert res.is_error is False
    assert res.usage is not None
    assert res.usage.tokens_in == 1200
    assert res.usage.cost_usd == pytest.approx(0.0042)
    # La cle reservee ne doit pas fuiter dans le contenu vu par le modele.
    assert "__tool_usage__" not in str(res.output)
    assert "resume" in str(res.output)


@pytest.mark.asyncio
async def test_loop_bills_tool_declared_usage_in_turn_cost():
    """Le cout d'un outil (analyse media) doit entrer dans le total du tour."""
    from jarvis_core.tools.registry import TOOL_USAGE_KEY

    class _CallingProvider(LLMProvider):
        name = "openrouter"

        def __init__(self) -> None:
            self.step = 0

        async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
            return {}

        async def complete(
            self,
            model: str,
            messages: list[Message],
            tools=None,
            fallback_models=None,
            temperature: float = 0.2,
        ) -> StreamDelta:
            self.step += 1
            usage = Usage(
                model=model, provider=self.name, tokens_in=10, tokens_out=5, cost_usd=0.002
            )
            if self.step == 1:
                return StreamDelta(
                    text="",
                    tool_calls=[ToolCall(id="t1", name="browser_analyze_media", arguments={})],
                    usage=usage,
                )
            return StreamDelta(text="Termine.", usage=usage)

        async def stream(self, model, messages, tools=None, fallback_models=None, temperature=0.2):
            yield await self.complete(model, messages, tools, fallback_models, temperature)

    policy = AutonomyPolicy(mode="autonomous")
    registry = ToolRegistry(autonomy_policy=policy)

    class _NoArgs(BaseModel):
        pass

    registry.register(
        ToolSpec(
            name="browser_analyze_media",
            description="Media",
            handler=lambda: {
                "ok": True,
                "analysis": "video analysee",
                TOOL_USAGE_KEY: Usage(
                    model="gemini-test",
                    provider="gemini",
                    tokens_in=5000,
                    tokens_out=700,
                    cost_usd=0.05,
                ),
            },
            args_model=_NoArgs,
            risk=RiskLevel.READ_ONLY,
            returns_untrusted=True,
        )
    )

    router = LLMRouter(providers={"openrouter": _CallingProvider()})
    loop = AgentLoop(router=router, tool_registry=registry, register_delegate_tool=False)
    res = await loop.run_turn("analyse la video de la page", autonomous_run=True)

    assert abs(sum(u.cost_usd for u in res.usages) - (0.002 + 0.002 + 0.05)) < 1e-9
    assert sum(u.tokens_in + u.tokens_out for u in res.usages) == 15 + 15 + 5700


@pytest.mark.parametrize("playwright_enabled", [False, True])
def test_playwright_mcp_and_builtin_browser_tools_are_mutually_exclusive(
    tmp_path: Path, playwright_enabled: bool
) -> None:
    """Une seule implementation du controle navigateur active a la fois."""
    from jarvis_core.runtime import build_runtime

    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "mcp.yaml").write_text(
        "servers:\n"
        "  playwright:\n"
        f"    enabled: {'true' if playwright_enabled else 'false'}\n"
        "    transport: stdio\n"
        "    command: echo\n",
        encoding="utf-8",
    )
    rt = build_runtime(data_dir=tmp_path / "data", config_dir=cfg, allow_offline=True)
    tool_names = {schema["function"]["name"] for schema in rt.tool_registry.list_schemas()}
    assert ("browser_snapshot" in tool_names) is (not playwright_enabled)
    if playwright_enabled:
        assert rt.browser_toolkit is None
    else:
        assert rt.browser_toolkit is not None


def test_format_ax_tree_marks_editable_zones_and_versions_refs():
    nodes = [
        {
            "nodeId": "1",
            "role": {"value": "RootWebArea"},
            "name": {"value": "Ed"},
            "childIds": ["2", "3"],
        },
        {
            "nodeId": "2",
            "role": {"value": "generic"},
            "backendDOMNodeId": 21,
            "properties": [{"name": "editable", "value": {"value": True}}],
            "childIds": ["2a"],
        },
        {
            "nodeId": "2a",
            "role": {"value": "StaticText"},
            "name": {"value": "texte"},
            "childIds": [],
        },
        {
            "nodeId": "3",
            "role": {"value": "button"},
            "name": {"value": "OK"},
            "backendDOMNodeId": 22,
            "childIds": [],
        },
    ]
    text, ref_map, next_offset = format_ax_tree(nodes, generation=4)
    assert next_offset == 0
    assert "[e1@4] editable_text (editable=true)" in text
    assert '[e2@4] button "OK"' in text
    assert ref_map == {"e1@4": 21, "e2@4": 22}
    # Le StaticText interne d'une zone editable ne doit plus etre duplique
    assert text.count('"texte"') == 0


def test_desktop_a11y_blacklist_and_autonomy_rest_endpoints(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JARVIS_ALLOW_INSECURE", "1")
    assert _is_blacklisted_app("KeePassXC") is True
    assert _is_blacklisted_app("Chromium", "Bitwarden Vault") is True
    assert _is_blacklisted_app("Kate", "notes.txt") is False
    # Verification de l'instanciation sans crash meme sans affichage
    _ = DesktopA11yToolkit()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "mcp.yaml").write_text(
            (Path(__file__).resolve().parents[1] / "config" / "mcp.yaml").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        state = JarvisCoreState(data_dir=tmp_path / "data", config_dir=cfg_dir)
        app = create_app(state)
        client = TestClient(app)

        pol_resp = client.get("/api/autonomy/policy")
        assert pol_resp.status_code == 200
        assert pol_resp.json()["mode"] == "supervised"

        upd = client.put(
            "/api/autonomy/policy",
            json={"mode": "autonomous", "allowed_domains": ["moodle.univ.fr"], "max_steps": 25},
        )
        assert upd.status_code == 200
        assert upd.json()["mode"] == "autonomous"
        assert upd.json()["allowed_domains"] == ["moodle.univ.fr"]

        mcp_resp = client.get("/api/mcp/servers")
        assert mcp_resp.status_code == 200
        names = {s["name"] for s in mcp_resp.json()}
        assert "playwright" in names

        stop_resp = client.post("/api/autonomy/emergency-stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["emergency_stop"] is True

        blocked_create = client.post("/api/autonomy/tasks", json={"goal": "Test bloque"})
        assert blocked_create.status_code == 409

        audit_resp = client.get("/api/autonomy/audit")
        assert audit_resp.status_code == 200


@pytest.mark.asyncio
async def test_webapp_shortcut_discovery_and_reuse(tmp_path: Path):
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()
    desktop_file = apps_dir / "chrome-music.apple.com__fr_home-Default.desktop"
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=Apple Music\n"
        "Exec=/usr/bin/chromium --remote-debugging-port=9222 --profile-directory=Default "
        "--ignore-profile-directory-if-not-exists --app=https://music.apple.com/fr/home\n"
        "StartupWMClass=chrome-music.apple.com__fr_home-Default\n",
        encoding="utf-8",
    )
    (apps_dir / "jarvis.desktop").write_text(
        "[Desktop Entry]\nName=JARVIS\nExec=/usr/bin/chromium --app=http://localhost:8765\n",
        encoding="utf-8",
    )

    toolkit = BrowserToolkit(cdp_url="http://127.0.0.1:9222", apps_dir=apps_dir)
    by_app = toolkit._find_webapp_shortcut(app="Apple Music")
    assert by_app is not None
    assert by_app.name == "Apple Music"
    assert by_app.domain == "music.apple.com"
    assert by_app.app_url == "https://music.apple.com/fr/home"

    by_url = toolkit._find_webapp_shortcut(url="https://music.apple.com/fr/library/playlists")
    assert by_url is not None
    assert by_url.desktop_id == "chrome-music.apple.com__fr_home-Default.desktop"
    assert toolkit._find_webapp_shortcut(url="http://localhost:8765") is None

    # Si l'appli web est deja ouverte dans CDP, browser_open_tab et browser_launch
    # activent cette fenetre au lieu d'appeler PUT /json/new.
    fake_tabs = [
        {
            "id": "tab-am",
            "type": "page",
            "title": "Apple Music",
            "url": "https://music.apple.com/fr/home",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab-am",
        }
    ]
    activated: list[str] = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            activated.append(url)
            return type("Resp", (), {"status_code": 200})()

        async def put(self, url: str):
            raise AssertionError(f"Ne doit pas ouvrir un nouvel onglet ({url})")

    from jarvis_core.tools.browser import BrowserLaunchArgs, BrowserOpenTabArgs

    with (
        patch.object(toolkit, "_fetch_tabs", AsyncMock(return_value=fake_tabs)),
        patch("jarvis_core.tools.browser.httpx.AsyncClient", _FakeAsyncClient),
    ):
        res_tab = await toolkit.browser_open_tab(
            BrowserOpenTabArgs(url="https://music.apple.com/fr/home")
        )
        assert res_tab["ok"] is True
        assert res_tab["already_open"] is True
        assert res_tab["tab_id"] == "tab-am"
        assert res_tab["web_app"] == "Apple Music"

        res_launch = await toolkit.browser_launch(BrowserLaunchArgs(app="Apple Music"))
        assert res_launch["ok"] is True
        assert res_launch["already_open"] is True
        assert res_launch["tab_id"] == "tab-am"
        assert len(activated) == 2


@pytest.mark.asyncio
async def test_webapp_shortcut_launches_in_app_mode_when_not_open(tmp_path: Path):
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()
    desktop_file = apps_dir / "chrome-music.apple.com__fr_home-Default.desktop"
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Name=Apple Music\n"
        "Exec=/usr/bin/chromium --profile-directory=Default --app=https://music.apple.com/fr/home\n"
        "StartupWMClass=chrome-music.apple.com__fr_home-Default\n",
        encoding="utf-8",
    )

    toolkit = BrowserToolkit(cdp_url="http://127.0.0.1:9222", apps_dir=apps_dir)
    launched_cmds: list[list[str]] = []

    def _fake_popen(cmd, **kwargs):
        launched_cmds.append(list(cmd))
        return type("Proc", (), {"poll": lambda self: None})()

    fetch_mock = AsyncMock(
        side_effect=[
            RuntimeError("no cdp yet"),
            [
                {
                    "id": "tab-am-new",
                    "type": "page",
                    "title": "Apple Music",
                    "url": "https://music.apple.com/fr/home",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab-am-new",
                }
            ],
        ]
    )

    from jarvis_core.tools.browser import BrowserLaunchArgs

    with (
        patch.object(toolkit, "_fetch_tabs", fetch_mock),
        patch.object(toolkit, "_wait_tab_stable", AsyncMock(return_value=True)),
        patch("jarvis_core.tools.browser.subprocess.Popen", side_effect=_fake_popen),
    ):
        res = await toolkit.browser_launch(BrowserLaunchArgs(app="Apple Music"))

    assert res["ok"] is True
    assert res["launched"] is True
    assert res["web_app"] == "Apple Music"
    assert res["tab_id"] == "tab-am-new"
    assert len(launched_cmds) == 1
    assert "--app=https://music.apple.com/fr/home" in launched_cmds[0]
    assert "--remote-debugging-port=9222" in launched_cmds[0]
