"""Outils agent pour les integrations Mail et Agenda (Gmail, Outlook, Google/Apple Calendar)."""

from __future__ import annotations

import os
from typing import Any, Literal

from omi_shared.models import RiskLevel
from pydantic import BaseModel, Field

from ..integrations.apple_calendar import AppleCalendarIntegration
from ..integrations.gmail import GmailIntegration
from ..integrations.google_calendar import GoogleCalendarIntegration
from ..integrations.outlook import OutlookCalendarIntegration, OutlookMailIntegration
from .registry import ToolSpec


class MailSearchArgs(BaseModel):
    query: str = Field(default="is:unread", description="Requete de recherche mail (ex: is:unread, from:alice).")
    max_results: int = Field(default=10, ge=1, le=25, description="Nombre maximal de messages.")
    provider: Literal["auto", "google", "outlook"] = Field(
        default="auto", description="Fournisseur cible (auto, google ou outlook)."
    )


class MailComposeArgs(BaseModel):
    to: str = Field(..., min_length=3, description="Adresse courriel du destinataire.")
    subject: str = Field(..., min_length=1, description="Objet du courriel.")
    body: str = Field(..., min_length=1, description="Contenu texte du courriel.")
    provider: Literal["auto", "google", "outlook"] = Field(
        default="auto", description="Fournisseur cible (auto, google ou outlook)."
    )


class CalendarListArgs(BaseModel):
    max_results: int = Field(default=10, ge=1, le=25, description="Nombre maximal d'evenements.")
    provider: Literal["auto", "google", "outlook", "apple"] = Field(
        default="auto", description="Fournisseur agenda (auto, google, outlook ou apple)."
    )


class CalendarCreateArgs(BaseModel):
    summary: str = Field(..., min_length=1, description="Titre de l'evenement.")
    start_iso: str = Field(..., min_length=10, description="Date/heure de debut ISO 8601.")
    end_iso: str = Field(..., min_length=10, description="Date/heure de fin ISO 8601.")
    provider: Literal["auto", "google", "outlook"] = Field(
        default="auto", description="Fournisseur cible (auto, google ou outlook)."
    )


class IntegrationToolkit:
    """Expose les outils Mail et Agenda connectes dans ToolRegistry."""

    @staticmethod
    def _google_token() -> str:
        return os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "").strip()

    @staticmethod
    def _microsoft_token() -> str:
        return os.environ.get("MICROSOFT_OAUTH_ACCESS_TOKEN", "").strip()

    @staticmethod
    def _apple_ics_url() -> str:
        return os.environ.get("APPLE_CALENDAR_ICS_URL", "").strip()

    def _pick_mail_connector(
        self, provider: str
    ) -> tuple[str, GmailIntegration | OutlookMailIntegration] | tuple[None, None]:
        g_tok = self._google_token()
        m_tok = self._microsoft_token()
        if provider == "google" or (provider == "auto" and g_tok):
            return ("google", GmailIntegration(access_token=g_tok, enabled=True)) if g_tok else (None, None)
        if provider == "outlook" or (provider == "auto" and m_tok):
            return ("outlook", OutlookMailIntegration(access_token=m_tok, enabled=True)) if m_tok else (None, None)
        return (None, None)

    async def mail_search(self, args: MailSearchArgs) -> dict[str, Any]:
        prov, connector = self._pick_mail_connector(args.provider)
        if connector is None:
            return {"ok": False, "error": "Aucun compte mail connecte (Gmail ou Outlook)."}
        if isinstance(connector, GmailIntegration):
            raw = await connector.list_messages(query=args.query, max_results=args.max_results)
            detailed = [await connector.get_message(m["id"]) for m in raw[: args.max_results] if m.get("id")]
            return {"ok": True, "provider": prov, "messages": detailed}
        raw = await connector.list_messages(max_results=args.max_results)
        return {"ok": True, "provider": prov, "messages": raw}

    async def mail_draft(self, args: MailComposeArgs) -> dict[str, Any]:
        prov, connector = self._pick_mail_connector(args.provider)
        if connector is None:
            return {"ok": False, "error": "Aucun compte mail connecte (Gmail ou Outlook)."}
        res = await connector.create_draft(args.to, args.subject, args.body)
        return {**res, "provider": prov}

    async def mail_send(self, args: MailComposeArgs) -> dict[str, Any]:
        prov, connector = self._pick_mail_connector(args.provider)
        if connector is None:
            return {"ok": False, "error": "Aucun compte mail connecte (Gmail ou Outlook)."}
        res = await connector.send_message(args.to, args.subject, args.body)
        return {**res, "provider": prov}

    async def calendar_list_events(self, args: CalendarListArgs) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        g_tok = self._google_token()
        m_tok = self._microsoft_token()
        a_url = self._apple_ics_url()

        if args.provider in ("auto", "google") and g_tok:
            items = await GoogleCalendarIntegration(access_token=g_tok, enabled=True).list_upcoming_events(
                max_results=args.max_results
            )
            events.extend({"provider": "google", **ev} for ev in items)
        if args.provider in ("auto", "outlook") and m_tok:
            items = await OutlookCalendarIntegration(access_token=m_tok, enabled=True).list_upcoming_events(
                max_results=args.max_results
            )
            events.extend({"provider": "outlook", **ev} for ev in items)
        if args.provider in ("auto", "apple") and a_url:
            items = await AppleCalendarIntegration(ics_url=a_url, enabled=True).list_upcoming_events(
                max_results=args.max_results
            )
            events.extend({"provider": "apple", **ev} for ev in items)

        if not (g_tok or m_tok or a_url):
            return {"ok": False, "error": "Aucun agenda connecte (Google, Outlook ou Apple)."}
        return {"ok": True, "events": events[: args.max_results]}

    async def calendar_create_event(self, args: CalendarCreateArgs) -> dict[str, Any]:
        g_tok = self._google_token()
        m_tok = self._microsoft_token()
        if args.provider == "google" or (args.provider == "auto" and g_tok):
            if not g_tok:
                return {"ok": False, "error": "Jeton Google Calendar manquant."}
            res = await GoogleCalendarIntegration(access_token=g_tok, enabled=True).create_event(
                args.summary, args.start_iso, args.end_iso
            )
            return {**res, "provider": "google"}
        if args.provider == "outlook" or (args.provider == "auto" and m_tok):
            if not m_tok:
                return {"ok": False, "error": "Jeton Outlook Calendar manquant."}
            res = await OutlookCalendarIntegration(access_token=m_tok, enabled=True).create_event(
                args.summary, args.start_iso, args.end_iso
            )
            return {**res, "provider": "outlook"}
        return {"ok": False, "error": "Aucun agenda editable connecte (Google ou Outlook)."}

    def register_all(self, registry: Any) -> None:
        """Enregistre les outils Mail et Agenda dans ToolRegistry."""
        specs: list[tuple[str, str, type[BaseModel], Any, RiskLevel, bool, str | None]] = [
            (
                "mail_search",
                "Recherche et lit les courriels recents (Gmail ou Outlook).",
                MailSearchArgs,
                self.mail_search,
                RiskLevel.READ_ONLY,
                True,
                "mail",
            ),
            (
                "mail_draft",
                "Cree un brouillon de courriel (Gmail ou Outlook) sans l'envoyer.",
                MailComposeArgs,
                self.mail_draft,
                RiskLevel.REVERSIBLE_WRITE,
                False,
                None,
            ),
            (
                "mail_send",
                "Envoie un courriel via Gmail ou Outlook (action sensible soumise a confirmation).",
                MailComposeArgs,
                self.mail_send,
                RiskLevel.SENSITIVE,
                False,
                None,
            ),
            (
                "calendar_list_events",
                "Liste les prochains rendez-vous de l'agenda (Google, Outlook ou Apple .ics).",
                CalendarListArgs,
                self.calendar_list_events,
                RiskLevel.READ_ONLY,
                True,
                "calendar",
            ),
            (
                "calendar_create_event",
                "Cree un nouvel evenement dans l'agenda (Google ou Outlook).",
                CalendarCreateArgs,
                self.calendar_create_event,
                RiskLevel.REVERSIBLE_WRITE,
                False,
                None,
            ),
        ]
        for name, desc, args_model, handler, risk, returns_untrusted, untrusted_source in specs:
            def _make(h: Any, model: type[BaseModel]) -> Any:
                async def _w(**kwargs: Any) -> dict[str, Any]:
                    parsed = kwargs if isinstance(kwargs, model) else model.model_validate(kwargs)
                    return await h(parsed)

                return _w

            registry.register(
                ToolSpec(
                    name=name,
                    description=desc,
                    handler=_make(handler, args_model),
                    args_model=args_model,
                    risk=risk,
                    returns_untrusted=returns_untrusted,
                    untrusted_source=untrusted_source,
                )
            )
