"""Outils d'ordres de notification et de politique pour le LLM."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from pydantic import BaseModel, Field

from omi_shared.models import RiskLevel
from omi_shared.notifications import NotificationOrder, NotificationPolicy
from ..memory.schema import MemoryDatabase
from ..notifications.policy import PolicyStore
from .registry import ToolSpec as RegistryToolSpec


class NotifyMeArgs(BaseModel):
    source_glob: str = Field(default="*", description="Origine de l'événement (ex. 'github:*', 'build', '*')")
    event_type_glob: str = Field(default="*", description="Type d'événement (ex. 'probe.failed', '*')")
    text_match: str | None = Field(default=None, description="Sous-chaîne ou texte à surveiller")
    min_urgency: int = Field(default=2, ge=0, le=4, description="Niveau d'urgence minimum (0-4)")
    channels: list[str] | None = Field(default=None, description="Canaux spécifiques ou None pour défaut")
    expires_at: str | None = Field(default=None, description="Date ISO 8601 d'expiration de l'ordre")
    max_fires: int | None = Field(default=None, ge=1, description="Nombre maximum de déclenchements")
    note: str = Field(default="", description="Explication lisible de l'ordre")


class ListMyOrdersArgs(BaseModel):
    active_only: bool = Field(default=True, description="Ne lister que les ordres actifs")


class RevokeOrderArgs(BaseModel):
    order_id: str = Field(description="Identifiant de l'ordre à révoquer (ex. ord_abc123)")


class SetNotificationPolicyArgs(BaseModel):
    allow_calls: bool | None = Field(default=None, description="Autoriser les appels vocaux normaux")
    allow_emergency_calls: bool | None = Field(default=None, description="Autoriser les appels d'extrême urgence (niveau 4)")
    quiet_start: str | None = Field(default=None, description="Début des heures calmes (HH:MM)")
    quiet_end: str | None = Field(default=None, description="Fin des heures calmes (HH:MM)")
    call_min_urgency: int | None = Field(default=None, ge=0, le=4, description="Niveau minimal d'appel (0-4)")
    snooze_until: str | None = Field(default=None, description="Pause temporaire ISO 8601 (ne pas déranger avant)")
    max_calls_per_day: int | None = Field(default=None, ge=0, le=20, description="Plafond quotidien d'appels")


class ShowNotificationPolicyArgs(BaseModel):
    pass


class NotificationToolkit:
    """Boîte à outils de gestion des ordres et de la politique de notification."""

    def __init__(self, database: MemoryDatabase | None = None) -> None:
        self.db = database

    async def notify_me(self, args: NotifyMeArgs) -> dict[str, Any]:
        if not self.db:
            return {"ok": False, "error": "Base de données non disponible."}
        order = NotificationOrder(
            source_glob=args.source_glob,
            event_type_glob=args.event_type_glob,
            text_match=args.text_match,
            min_urgency=args.min_urgency,
            channels_json=json.dumps(args.channels or []),
            expires_at=args.expires_at,
            max_fires=args.max_fires,
            note=args.note or (f"Prévenir quand '{args.text_match}'" if args.text_match else "Ordre actif"),
        )
        self.db.upsert_order(order.model_dump())
        summary = (
            f"Ordre #{order.id} actif : notification si "
            f"'{order.text_match or order.event_type_glob}' survient "
            f"(urgence {order.min_urgency})"
            f"{f' jusqu au {order.expires_at}' if order.expires_at else ''}."
        )
        return {"ok": True, "order_id": order.id, "summary": summary}

    async def list_my_orders(self, args: ListMyOrdersArgs) -> dict[str, Any]:
        if not self.db:
            return {"ok": False, "error": "Base de données non disponible."}
        orders = self.db.list_orders(active_only=args.active_only)
        return {"ok": True, "orders": orders, "count": len(orders)}

    async def revoke_order(self, args: RevokeOrderArgs) -> dict[str, Any]:
        if not self.db:
            return {"ok": False, "error": "Base de données non disponible."}
        revoked = self.db.revoke_order(args.order_id)
        if revoked:
            return {"ok": True, "summary": f"Ordre #{args.order_id} révoqué avec succès."}
        return {"ok": False, "error": f"Ordre #{args.order_id} introuvable."}

    async def set_notification_policy(self, args: SetNotificationPolicyArgs) -> dict[str, Any]:
        if not self.db:
            return {"ok": False, "error": "Base de données non disponible."}
        store = PolicyStore(self.db)
        updates = args.model_dump(exclude_none=True)
        updated = store.update(**updates)
        return {
            "ok": True,
            "summary": "Politique de notification mise à jour avec succès.",
            "policy": updated.model_dump(),
        }

    async def show_notification_policy(self, args: ShowNotificationPolicyArgs) -> dict[str, Any]:
        if not self.db:
            return {"ok": False, "error": "Base de données non disponible."}
        store = PolicyStore(self.db)
        policy = store.get()
        return {"ok": True, "policy": policy.model_dump()}

    def register_all(self, registry: Any) -> None:
        """Enregistre les outils de notification dans ToolRegistry."""
        tools = [
            (
                "notify_me",
                "Enregistre un ordre de notification persistant pour être prévenu sur des événements futurs.",
                NotifyMeArgs,
                self.notify_me,
                RiskLevel.REVERSIBLE_WRITE,
            ),
            (
                "list_my_orders",
                "Liste les ordres de notification actuellement enregistrés et actifs.",
                ListMyOrdersArgs,
                self.list_my_orders,
                RiskLevel.READ_ONLY,
            ),
            (
                "revoke_order",
                "Révoque et désactive un ordre de notification persistant existant.",
                RevokeOrderArgs,
                self.revoke_order,
                RiskLevel.REVERSIBLE_WRITE,
            ),
            (
                "set_notification_policy",
                "Modifie la politique de notification globale (appels, heures calmes, plafond).",
                SetNotificationPolicyArgs,
                self.set_notification_policy,
                RiskLevel.REVERSIBLE_WRITE,
            ),
            (
                "show_notification_policy",
                "Affiche la politique de notification effective et actuelle.",
                ShowNotificationPolicyArgs,
                self.show_notification_policy,
                RiskLevel.READ_ONLY,
            ),
        ]
        for name, desc, args_model, handler, risk in tools:
            def _make(h: Any) -> Any:
                async def _w(**kwargs: Any) -> dict[str, Any]:
                    parsed = kwargs
                    if isinstance(kwargs, dict) and not isinstance(kwargs, args_model):
                        parsed = args_model.model_validate(kwargs)
                    return await h(parsed)
                return _w

            spec = RegistryToolSpec(
                name=name,
                description=desc,
                handler=_make(handler),
                args_model=args_model,
                risk=risk,
            )
            registry.register(spec)
