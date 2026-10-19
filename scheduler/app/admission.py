from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from time import monotonic

from .config import BackendSpec, GatewayConfig
from .registry import BackendRegistry, Reservation


class QueueTimeoutError(TimeoutError):
    pass


class QueueFullError(RuntimeError):
    pass


class ConfigurationChangedError(RuntimeError):
    pass


@dataclass(slots=True)
class QueueTicket:
    request_id: str
    model_alias: str
    principal_id: str
    request_class: str
    enqueued_at: float


class FairLaneQueue:
    """Round-robin queue across principals, FIFO within each principal."""

    def __init__(self) -> None:
        self._by_principal: dict[str, deque[QueueTicket]] = defaultdict(deque)
        self._principals: deque[str] = deque()
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def principal_depth(self, principal_id: str) -> int:
        return len(self._by_principal.get(principal_id, ()))

    def append(self, ticket: QueueTicket) -> None:
        queue = self._by_principal[ticket.principal_id]
        if not queue:
            self._principals.append(ticket.principal_id)
        queue.append(ticket)
        self._size += 1

    def peek_eligible(self, blocked_principals: set[str]) -> QueueTicket | None:
        for principal_id in self._principals:
            if principal_id not in blocked_principals:
                return self._by_principal[principal_id][0]
        return None

    def pop(self, ticket: QueueTicket) -> QueueTicket:
        principal_id = ticket.principal_id
        self._principals.remove(principal_id)
        queue = self._by_principal[principal_id]
        assert queue.popleft() is ticket
        self._size -= 1
        if queue:
            self._principals.append(principal_id)
        else:
            self._by_principal.pop(principal_id, None)
        return ticket

    def remove(self, ticket: QueueTicket) -> bool:
        queue = self._by_principal.get(ticket.principal_id)
        if queue is None:
            return False
        try:
            queue.remove(ticket)
        except ValueError:
            return False
        self._size -= 1
        if not queue:
            self._by_principal.pop(ticket.principal_id, None)
            try:
                self._principals.remove(ticket.principal_id)
            except ValueError:
                pass
        return True

class AdmissionController:
    def __init__(
        self,
        registry: BackendRegistry,
        *,
        max_queue_depth: int = 50,
        max_queued_per_principal: int = 2,
        max_active_per_principal: int = 1,
        long_queue_promote_seconds: float = 120.0,
    ) -> None:
        if (
            max_queue_depth <= 0
            or max_queued_per_principal <= 0
            or max_active_per_principal <= 0
        ):
            raise ValueError("queue limits must be positive")
        if long_queue_promote_seconds < 0:
            raise ValueError("long_queue_promote_seconds cannot be negative")
        self.registry = registry
        self.max_queue_depth = max_queue_depth
        self.max_queued_per_principal = max_queued_per_principal
        self.max_active_per_principal = max_active_per_principal
        self.long_queue_promote_seconds = long_queue_promote_seconds
        self._active_by_principal: dict[tuple[str, str], int] = defaultdict(int)
        self._condition = asyncio.Condition()
        self._queues: dict[str, dict[str, FairLaneQueue]] = defaultdict(
            lambda: {
                "interactive": FairLaneQueue(),
                "long": FairLaneQueue(),
            }
        )

    async def acquire(
        self,
        request_id: str,
        model_alias: str,
        principal_id: str,
        request_class: str,
        timeout_seconds: float,
        *,
        config_revision: int | None = None,
    ) -> Reservation:
        if request_class not in {"interactive", "long"}:
            raise ValueError(f"unknown request class: {request_class}")
        ticket = QueueTicket(
            request_id=request_id,
            model_alias=model_alias,
            principal_id=principal_id,
            request_class=request_class,
            enqueued_at=monotonic(),
        )
        deadline = ticket.enqueued_at + timeout_seconds
        async with self._condition:
            expected_revision = (
                self.registry.config_revision
                if config_revision is None
                else config_revision
            )
            if expected_revision != self.registry.config_revision:
                raise ConfigurationChangedError("model configuration changed; retry request")
            queues = self._queues[model_alias]
            lane = queues[request_class]
            if self._model_depth(queues) >= self.max_queue_depth:
                raise QueueFullError("model queue is full")
            if self._principal_depth(queues, principal_id) >= self.max_queued_per_principal:
                raise QueueFullError("principal queue limit is reached")
            lane.append(ticket)
            try:
                while True:
                    if expected_revision != self.registry.config_revision:
                        raise ConfigurationChangedError(
                            "model configuration changed; retry request"
                        )
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise QueueTimeoutError(
                            f"request waited more than {timeout_seconds:g} seconds"
                        )
                    selected_ticket = self._selected_ticket(model_alias, queues)
                    if selected_ticket is ticket:
                        reservation = self.registry.reserve(
                            request_id,
                            model_alias,
                            principal_id,
                            request_class,
                        )
                        if reservation is not None:
                            assert lane.pop(ticket) is ticket
                            active_key = (model_alias, principal_id)
                            self._active_by_principal[active_key] += 1
                            self._remove_empty_model(model_alias)
                            self._condition.notify_all()
                            return reservation
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                    except TimeoutError as exc:
                        raise QueueTimeoutError(
                            f"request waited more than {timeout_seconds:g} seconds"
                        ) from exc
            finally:
                queues = self._queues.get(model_alias)
                if queues is not None and queues[request_class].remove(ticket):
                    self._remove_empty_model(model_alias)
                    self._condition.notify_all()

    async def release(self, reservation: Reservation) -> None:
        async with self._condition:
            if reservation.released:
                return
            self.registry.release(reservation)
            active_key = (reservation.model_alias, reservation.principal_id)
            active = self._active_by_principal.get(active_key, 0)
            if active <= 1:
                self._active_by_principal.pop(active_key, None)
            else:
                self._active_by_principal[active_key] = active - 1
            self._condition.notify_all()

    async def mark_health(
        self,
        backend_id: str,
        healthy: bool,
        error: str | None = None,
        *,
        expected_backend: BackendSpec | None = None,
    ) -> bool:
        async with self._condition:
            applied = self.registry.mark_health(
                backend_id, healthy, error, expected_backend=expected_backend
            )
            if applied:
                self._condition.notify_all()
            return applied

    async def reload(self, config: GatewayConfig) -> None:
        async with self._condition:
            self.registry.reload(config)
            self._condition.notify_all()

    def queue_depths(self) -> dict[str, dict[str, int]]:
        return {
            alias: {request_class: len(lane) for request_class, lane in lanes.items()}
            for alias, lanes in self._queues.items()
            if self._model_depth(lanes)
        }

    def _selected_ticket(
        self, model_alias: str, queues: dict[str, FairLaneQueue]
    ) -> QueueTicket | None:
        blocked = {
            principal_id
            for (alias, principal_id), active in self._active_by_principal.items()
            if alias == model_alias and active >= self.max_active_per_principal
        }
        interactive = queues["interactive"]
        long = queues["long"]
        interactive_ticket = interactive.peek_eligible(blocked)
        long_ticket = long.peek_eligible(blocked)
        if interactive_ticket is not None and not self.registry.has_capacity(
            model_alias, "interactive"
        ):
            interactive_ticket = None
        if long_ticket is not None and not self.registry.has_capacity(
            model_alias, "long"
        ):
            long_ticket = None
        if long_ticket is not None and (
            interactive_ticket is None
            or monotonic() - long_ticket.enqueued_at >= self.long_queue_promote_seconds
        ):
            return long_ticket
        return interactive_ticket or long_ticket

    @staticmethod
    def _model_depth(queues: dict[str, FairLaneQueue]) -> int:
        return sum(len(queue) for queue in queues.values())

    @staticmethod
    def _principal_depth(
        queues: dict[str, FairLaneQueue], principal_id: str
    ) -> int:
        return sum(queue.principal_depth(principal_id) for queue in queues.values())

    def _remove_empty_model(self, model_alias: str) -> None:
        queues = self._queues.get(model_alias)
        if queues is not None and self._model_depth(queues) == 0:
            self._queues.pop(model_alias, None)
