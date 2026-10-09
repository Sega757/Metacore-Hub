"""Glue: runs the arbitration step (arbiter proposes -> engine disposes)."""
from __future__ import annotations
from .engine import CoreEngine
from .fsm import TaskState


def arbitrate(engine: CoreEngine, arbiter, task_id: str) -> TaskState:
    t = engine.tasks[task_id]
    assert t.state == TaskState.ARBITER_VALIDATING, f"{task_id} is {t.state.value}"
    verdict = arbiter.review(task_id, t.last_patch, t.payload["files"])
    return engine.handle_event(verdict) or t.state
