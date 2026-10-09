"""JSON Schema (Draft 2020-12) contracts. Anything that fails goes to the DLQ."""
from __future__ import annotations
from jsonschema import Draft202012Validator

TASK_DISPATCH = {
    "type": "object",
    "required": ["repo", "branch", "prompt", "files"],
    "additionalProperties": False,
    "properties": {
        "repo": {"type": "string", "minLength": 1},
        "branch": {"type": "string", "pattern": r"^(?!main$|master$)[\w./-]+$"},
        "prompt": {"type": "string", "minLength": 1},
        "files": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "require_tests": {"type": "boolean"},
    },
}

PATCH_READY = {
    "type": "object",
    "required": ["type", "task_id", "diff_files", "tests"],
    "properties": {
        "type": {"const": "patch_ready"},
        "task_id": {"type": "string"},
        "diff_files": {"type": "array", "items": {"type": "string"}},
        "diff": {"type": "string"},
        "tests": {
            "type": "object",
            "required": ["passed", "failed"],
            "properties": {"passed": {"type": "integer", "minimum": 0},
                           "failed": {"type": "integer", "minimum": 0},
                           "log": {"type": "string"}},
        },
    },
}

ARBITER_VERDICT = {
    "type": "object",
    "required": ["type", "task_id", "verdict", "confidence"],
    "properties": {
        "type": {"const": "arbiter_verdict"},
        "task_id": {"type": "string"},
        "verdict": {"enum": ["APPROVE", "REWORK", "ESCALATE_HUMAN"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "rework_instructions": {"type": "string"},
    },
    "allOf": [{"if": {"properties": {"verdict": {"const": "REWORK"}}},
               "then": {"required": ["rework_instructions"]}}],
}

SIMPLE_EVENT = {
    "type": "object",
    "required": ["type", "task_id"],
    "properties": {"type": {"enum": ["worker_started", "worker_activity", "worker_failed",
                                     "human_decision", "cancel"]},
                   "task_id": {"type": "string"},
                   "decision": {"enum": ["APPROVE", "REWORK", "CANCEL"]},
                   "instructions": {"type": "string"}},
}

SCHEMAS = {"patch_ready": PATCH_READY, "arbiter_verdict": ARBITER_VERDICT}
_validators = {k: Draft202012Validator(v) for k, v in SCHEMAS.items()}
_simple = Draft202012Validator(SIMPLE_EVENT)
_dispatch = Draft202012Validator(TASK_DISPATCH)


class ContractViolation(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _run(v: Draft202012Validator, obj) -> None:
    errs = sorted(e.message for e in v.iter_errors(obj))
    if errs:
        raise ContractViolation(errs)


def validate_dispatch(payload: dict) -> None:
    _run(_dispatch, payload)


def validate_event(event: dict) -> None:
    if not isinstance(event, dict):
        raise ContractViolation(["event must be an object"])
    _run(_validators.get(event.get("type"), _simple), event)
