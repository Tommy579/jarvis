"""Bus d'evenements interne asynchrone."""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from omi_shared.models import SystemEvent


class EventBus:
    """Bus pub/sub asynchrone alimentant le moteur d'attention."""

    def __init__(self, storage: Any = None):
        self.storage = storage
        self._subscribers: dict[str, list[Callable[[SystemEvent], Any]]] = defaultdict(list)

    def subscribe(self, event_type_or_wildcard: str, handler: Callable[[SystemEvent], Any]) -> None:
        self._subscribers[event_type_or_wildcard].append(handler)

    async def publish(self, event: SystemEvent) -> SystemEvent:
        if self.storage is not None:
            self.storage.insert_event(event)
        handlers = list(self._subscribers.get(event.event_type, [])) + list(
            self._subscribers.get("*", [])
        )
        for h in handlers:
            if inspect.iscoroutinefunction(h):
                await h(event)
            else:
                res = h(event)
                if asyncio.iscoroutine(res):
                    await res
        return event
