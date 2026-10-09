"""Core Engine Daemon: sole owner of task state. The LLM only emits proposals."""
from __future__ import annotations
import itertools, time
from dataclasses import dataclass
from typing import Callable, Protocol

from . import contracts
from .audit import AuditLog
from .fsm import TaskState, check, TERMINAL


class Worker(Protocol):
    def start(self, task_id: str, payload: dict) -> None: ...
    def rework(self, task_id: str, instructions: str) -> None: ...
    def nudge(self, task_id: str, level: int) -> None: ...


@dataclass
class EnginePolicy:
    auto_merge_threshold: float = 0.85
    max_rework: int = 3
    hitl_mode: bool = False


@dataclass
class Task:
    id: str
    payload: dict
    state: TaskState = TaskState.DISPATCHED
    rework_count: int = 0
    last_patch: dict | None = None
    last_activity: float = 0.0
    nudges: int = 0
    hitl: bool = False


class CoreEngine:
    def __init__(self, worker: Worker, policy: EnginePolicy | None = None,
                 merger: Callable[[Task], None] | None = None,
                 clock: Callable[[], float] | None = None,
                 audit: AuditLog | None = None):
        self.worker, self.policy = worker, policy or EnginePolicy()
        self.merger = merger or (lambda t: None)
        self.clock = clock or time.monotonic
        self.audit = audit or AuditLog()
        self.tasks: dict[str, Task] = {}
        self.dlq: list[dict] = []
        self._ids = itertools.count(1)

    # ---- the ONLY place state changes ------------------------------------
    def _move(self, t: Task, dst: TaskState, why: str) -> None:
        check(t.state, dst)
        self.audit.append("transition", task=t.id, src=t.state.value, dst=dst.value, why=why)
        t.state = dst

    def dispatch_task(self, repo: str, branch: str, prompt: str, files: list[str],
                      require_tests: bool = True, hitl: bool | None = None) -> str:
        payload = {"repo": repo, "branch": branch, "prompt": prompt, "files": files,
                   "require_tests": require_tests}
        contracts.validate_dispatch(payload)
        tid = f"task-{next(self._ids)}"
        self.tasks[tid] = Task(tid, payload, last_activity=self.clock(),
                               hitl=self.policy.hitl_mode if hitl is None else hitl)
        self.audit.append("dispatch", task=tid, payload=payload)
        self.worker.start(tid, payload)
        return tid

    def handle_event(self, event: dict) -> TaskState | None:
        """Schema-validate first; invalid or misrouted events go to the DLQ, state untouched."""
        try:
            contracts.validate_event(event)
        except contracts.ContractViolation as e:
            self.dlq.append({"event": event, "errors": e.errors})
            self.audit.append("dlq", errors=e.errors)
            return None
        t = self.tasks.get(event["task_id"])
        if t is None or t.state in TERMINAL:
            self.dlq.append({"event": event, "errors": ["unknown or terminal task"]})
            return None
        t.last_activity, t.nudges = self.clock(), 0
        getattr(self, "_on_" + event["type"])(t, event)
        return t.state

    # ---- handlers --------------------------------------------------------
    def _on_worker_started(self, t, e):
        if t.state == TaskState.DISPATCHED:
            self._move(t, TaskState.IN_PROGRESS, "worker started")

    def _on_worker_activity(self, t, e):
        pass  # heartbeat; timestamp already refreshed

    def _on_worker_failed(self, t, e):
        self._move(t, TaskState.FAILED, "worker failed")

    def _on_cancel(self, t, e):
        self._move(t, TaskState.CANCELLED, "cancel")

    def _on_patch_ready(self, t, e):
        t.last_patch = e
        if t.state == TaskState.DISPATCHED:
            self._move(t, TaskState.IN_PROGRESS, "implicit start")
        self._move(t, TaskState.ARBITER_VALIDATING, "patch ready")

    def _evidence_problems(self, t: Task) -> list[str]:
        """Deterministic gates run by the daemon itself; the LLM cannot waive them."""
        p, problems = t.last_patch or {}, []
        outside = [f for f in p.get("diff_files", []) if f not in set(t.payload["files"])]
        if outside:
            problems.append(f"files outside allowed set: {outside}")
        tests = p.get("tests", {})
        if t.payload.get("require_tests", True) and (tests.get("failed", 1) > 0 or tests.get("passed", 0) == 0):
            problems.append("tests not green")
        return problems

    def _on_arbiter_verdict(self, t, e):
        if t.state != TaskState.ARBITER_VALIDATING:
            self.dlq.append({"event": e, "errors": [f"verdict in state {t.state.value}"]})
            return
        v, c = e["verdict"], e["confidence"]
        if v == "APPROVE":
            problems = self._evidence_problems(t)
            if problems:
                self._move(t, TaskState.HUMAN_REQUIRED, "evidence gate: " + "; ".join(problems))
            elif t.hitl or c < self.policy.auto_merge_threshold:
                self._move(t, TaskState.HUMAN_REQUIRED, f"hitl={t.hitl} confidence={c}")
            else:
                self.merger(t)
                self._move(t, TaskState.MERGED, f"auto-merge c={c}")
        elif v == "REWORK":
            t.rework_count += 1
            if t.rework_count > self.policy.max_rework:
                self._move(t, TaskState.HUMAN_REQUIRED, "rework limit")
            else:
                self._move(t, TaskState.IN_PROGRESS, "rework")
                self.worker.rework(t.id, e["rework_instructions"])
        else:
            self._move(t, TaskState.HUMAN_REQUIRED, "arbiter escalated")

    def _on_human_decision(self, t, e):
        if t.state != TaskState.HUMAN_REQUIRED:
            self.dlq.append({"event": e, "errors": ["not awaiting human"]})
            return
        d = e.get("decision")
        if d == "APPROVE":
            self.merger(t)
            self._move(t, TaskState.MERGED, "human approved")
        elif d == "REWORK":
            self._move(t, TaskState.IN_PROGRESS, "human rework")
            self.worker.rework(t.id, e.get("instructions", ""))
        else:
            self._move(t, TaskState.CANCELLED, "human cancelled")

    def trip_breaker(self, task_id: str, reason: str) -> None:
        t = self.tasks[task_id]
        if t.state not in TERMINAL and t.state != TaskState.HUMAN_REQUIRED:
            self._move(t, TaskState.HUMAN_REQUIRED, "circuit breaker: " + reason)
