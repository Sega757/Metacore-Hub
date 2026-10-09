"""Append-only, hash-chained audit log (unsampled, separate from telemetry)."""
from __future__ import annotations
import hashlib, json, time
from pathlib import Path


def _h(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.entries: list[dict] = []
        self._prev = "0" * 64

    def append(self, kind: str, **data) -> dict:
        body = {"ts": time.time(), "kind": kind, **data, "prev": self._prev}
        body["hash"] = _h(body)
        self._prev = body["hash"]
        self.entries.append(body)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(body, default=str) + "\n")
        return body

    def verify(self) -> bool:
        prev = "0" * 64
        for e in self.entries:
            body = {k: v for k, v in e.items() if k != "hash"}
            if body["prev"] != prev or _h(body) != e["hash"]:
                return False
            prev = e["hash"]
        return True
