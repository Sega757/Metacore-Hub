"""In-memory worker stand-in for Jules-style cloud workers (for tests and demos)."""
from __future__ import annotations


class MockWorker:
    def __init__(self):
        self.started: list[str] = []
        self.reworks: list[tuple[str, str]] = []
        self.nudges: list[tuple[str, int]] = []

    def start(self, task_id, payload): self.started.append(task_id)
    def rework(self, task_id, instructions): self.reworks.append((task_id, instructions))
    def nudge(self, task_id, level): self.nudges.append((task_id, level))
