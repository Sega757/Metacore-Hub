"""Arbiters turn execution artifacts into *proposals* (verdict events). They never touch state."""
from __future__ import annotations
import json, re
from typing import Callable

from . import contracts


def _verdict(task_id, verdict, confidence, reasons, instructions=None) -> dict:
    ev = {"type": "arbiter_verdict", "task_id": task_id, "verdict": verdict,
          "confidence": confidence, "reasons": reasons}
    if instructions:
        ev["rework_instructions"] = instructions
    return ev


class RuleBasedArbiter:
    """Deterministic arbiter: usable offline, in CI, and as a baseline for the LLM one."""
    def review(self, task_id: str, patch: dict, allowed_files: list[str]) -> dict:
        reasons = []
        t = patch.get("tests", {})
        if t.get("failed", 0) > 0:
            return _verdict(task_id, "REWORK", 0.95, [f"{t['failed']} failing tests"],
                            "Fix failing tests. Log tail:\n" + t.get("log", "")[-800:])
        outside = [f for f in patch.get("diff_files", []) if f not in allowed_files]
        if outside:
            return _verdict(task_id, "REWORK", 0.95, ["out-of-scope files"],
                            f"Revert changes to: {outside}")
        if t.get("passed", 0) == 0:
            return _verdict(task_id, "ESCALATE_HUMAN", 0.5, ["no tests executed"])
        size = len(patch.get("diff", ""))
        conf = 0.95 if size < 4000 else 0.8  # big diffs need a human
        return _verdict(task_id, "APPROVE", conf, reasons or ["tests green, scope respected"])


SYSTEM_PROMPT = (
    "You are a code-review arbiter. Reply with ONE JSON object only: "
    '{"verdict":"APPROVE|REWORK|ESCALATE_HUMAN","confidence":0..1,"reasons":[...],'
    '"rework_instructions":"required if REWORK"}. You only propose; a daemon decides.'
)


class GeminiArbiter:
    """LLM arbiter. `llm` is any callable(prompt)->str, so it is testable without network.
    Use GeminiArbiter.from_google_genai() for the real thing (pip install google-genai)."""
    def __init__(self, llm: Callable[[str], str], fallback: RuleBasedArbiter | None = None):
        self.llm, self.fallback = llm, fallback or RuleBasedArbiter()

    @classmethod
    def from_google_genai(cls, model: str = "gemini-2.5-pro", api_key: str | None = None):
        from google import genai  # lazy import
        client = genai.Client(api_key=api_key)
        def call(prompt: str) -> str:
            r = client.models.generate_content(model=model, contents=prompt,
                                               config={"system_instruction": SYSTEM_PROMPT,
                                                       "response_mime_type": "application/json"})
            return r.text
        return cls(call)

    def review(self, task_id: str, patch: dict, allowed_files: list[str]) -> dict:
        prompt = json.dumps({"allowed_files": allowed_files,
                             "diff_files": patch.get("diff_files"),
                             "tests": patch.get("tests"),
                             "diff": patch.get("diff", "")[:60_000]})
        try:
            raw = self.llm(prompt)
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0))
            ev = {"type": "arbiter_verdict", "task_id": task_id, **data}
            contracts.validate_event(ev)
            return ev
        except Exception as e:  # bad JSON / schema / network -> deterministic fallback, never crash
            fb = self.fallback.review(task_id, patch, allowed_files)
            fb["reasons"].append(f"llm arbiter unusable ({type(e).__name__}); fell back to rules")
            fb["confidence"] = min(fb["confidence"], 0.8)  # force human gate on fallback approvals
            return fb
