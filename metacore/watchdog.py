"""Watchdog: nudges stalled workers, trips a circuit breaker after N nudges."""
from __future__ import annotations
from .engine import CoreEngine
from .fsm import TaskState


class Watchdog:
    def __init__(self, engine: CoreEngine, stall_seconds: float = 120, max_nudges: int = 3):
        self.engine, self.stall, self.max_nudges = engine, stall_seconds, max_nudges

    def tick(self) -> list[tuple[str, str]]:
        """Call periodically. Returns actions: (task_id, 'nudge1'|'nudge2'|'breaker')."""
        now, acts = self.engine.clock(), []
        for t in list(self.engine.tasks.values()):
            if t.state != TaskState.IN_PROGRESS or now - t.last_activity < self.stall:
                continue
            t.nudges += 1
            t.last_activity = now  # restart stall window after each intervention
            if t.nudges >= self.max_nudges:
                self.engine.trip_breaker(t.id, f"{t.nudges} nudges without progress")
                acts.append((t.id, "breaker"))
            else:
                self.engine.worker.nudge(t.id, t.nudges)  # 1=continue, 2=diagnostic
                acts.append((t.id, f"nudge{t.nudges}"))
        return acts
