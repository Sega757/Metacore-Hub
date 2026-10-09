"""Task FSM. Owned exclusively by the Core Engine: the LLM never mutates state."""
from __future__ import annotations
from enum import Enum


class TaskState(str, Enum):
    DISPATCHED = "DISPATCHED"
    IN_PROGRESS = "IN_PROGRESS"
    ARBITER_VALIDATING = "ARBITER_VALIDATING"
    MERGED = "MERGED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {TaskState.MERGED, TaskState.FAILED, TaskState.CANCELLED}

_S = TaskState
TRANSITIONS: dict[TaskState, set[TaskState]] = {
    _S.DISPATCHED: {_S.IN_PROGRESS, _S.FAILED, _S.CANCELLED, _S.HUMAN_REQUIRED},
    _S.IN_PROGRESS: {_S.ARBITER_VALIDATING, _S.FAILED, _S.CANCELLED, _S.HUMAN_REQUIRED},
    _S.ARBITER_VALIDATING: {_S.MERGED, _S.IN_PROGRESS, _S.HUMAN_REQUIRED, _S.CANCELLED},
    _S.HUMAN_REQUIRED: {_S.IN_PROGRESS, _S.MERGED, _S.CANCELLED, _S.FAILED},
    _S.MERGED: set(),
    _S.FAILED: set(),
    _S.CANCELLED: set(),
}


class IllegalTransition(Exception):
    pass


def check(src: TaskState, dst: TaskState) -> None:
    if dst not in TRANSITIONS[src]:
        raise IllegalTransition(f"{src.value} -> {dst.value} is not allowed")
