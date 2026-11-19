"""Tests de persistance des integrations actives et du diagnostic 403 Google Contacts."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from jarvis_core.api.server import JarvisCoreState, create_app
from jarvis_core.integrations.google_contacts import GoogleContactsIntegration
from jarvis_core.memory.schema import MemoryDatabase


class IntegrationsPersistenceTests(unittest.TestCase):
    def test_integrations_enabled_state_persists_across_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            state1 = JarvisCoreState(data_dir=data_dir)
            client1 = TestClient(create_app(state1))

            resp = client1.post("/api/integrations/gmail/toggle")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["enabled"])

            sync_resp = client1.post("/api/integrations/google_calendar/sync")
            self.assertEqual(sync_resp.status_code, 200)
            self.assertTrue(sync_resp.json()["enabled"])
            self.assertIsNotNone(sync_resp.json()["last_sync"])

            # Simule un redemarrage complet sur le meme data_dir
            state2 = JarvisCoreState(data_dir=data_dir)
            by_id = {item.id: item for item in state2.integrations.list_info()}
            self.assertTrue(by_id["gmail"].enabled)
            self.assertTrue(by_id["google_calendar"].enabled)
            self.assertIsNotNone(by_id["google_calendar"].last_sync)
            self.assertFalse(by_id["google_contacts"].enabled)

    def test_google_contacts_403_returns_clear_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = MemoryDatabase(Path(tmp) / "jarvis.db")
            connector = GoogleContactsIntegration(access_token="fake-token", enabled=True)

            req = httpx.Request(
                "GET",
                "https://people.googleapis.com/v1/people/me/connections",
            )
            disabled_resp = httpx.Response(
                403,
                request=req,
                json={
                    "error": {
                        "code": 403,
                        "message": "People API has not been used in project 123 or it is disabled.",
                    }
                },
            )
            err = httpx.HTTPStatusError("403 Forbidden", request=req, response=disabled_resp)
            with patch.object(connector, "_oauth_request", new=AsyncMock(side_effect=err)):
                res = asyncio.run(connector.sync(db))
            self.assertFalse(res["ok"])
            self.assertIn("Google People API désactivée", res["error"])


if __name__ == "__main__":
    unittest.main()
