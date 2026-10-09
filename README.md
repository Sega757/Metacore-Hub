# MetaCore Hub

**A daemon-owned task state machine for LLM-driven engineering agents.**

> *The LLM does not get a vote in state management.*

MetaCore Hub sits between non-deterministic AI generation (cloud coding workers, LLM reviewers) and deterministic
system commitments (merging code). The LLM *proposes*; a schema-validating daemon *disposes*.

---

## Why

LLM agents are good at reasoning and bad at being the source of truth. Letting a model flip a task to "approved" or
"merged" invites hallucinated approvals, context drift and runaway loops. MetaCore Hub fixes the boundary:

| Concern | Who owns it |
|---|---|
| Strategic reasoning, task decomposition, code review | Arbiter (LLM or rules) — **proposals only** |
| Task state, transitions, merge decisions | `CoreEngine` daemon — **sole owner** |
| Input trust | JSON Schema contracts; violations go to a dead-letter queue |
| Stalled or looping workers | `Watchdog` with a circuit breaker |
| Accountability | Hash-chained, tamper-evident audit log |

## Architecture

```
                 proposals (typed JSON)                 commitments
  ┌──────────┐  arbiter_verdict / dispatch   ┌──────────────────────┐   merge
  │ Arbiter  │ ────────────────────────────▶ │  CoreEngine (daemon) │ ───────▶ main
  │ LLM/rules│ ◀──────────────────────────── │  FSM + schema + gates│
  └──────────┘      patch_ready, tests        └──────────┬───────────┘
                                                         │ start / rework / nudge
                                              ┌──────────▼───────────┐
                                              │  Worker (e.g. Jules) │
                                              └──────────────────────┘
                         Watchdog ── stall > 120s ──▶ nudge ×2 ──▶ circuit breaker
```

### Task lifecycle

```
DISPATCHED ─▶ IN_PROGRESS ─▶ ARBITER_VALIDATING ─▶ MERGED
                  ▲   │              │   │
                  │   │              │   └──▶ HUMAN_REQUIRED ─▶ MERGED / IN_PROGRESS / CANCELLED
                  └───┼── REWORK ────┘
                      └──▶ FAILED / CANCELLED
```

### Merge policy

An `APPROVE` verdict merges automatically only if **all** hold:

1. `confidence ≥ 0.85` (configurable)
2. HITL mode is off
3. Engine-side evidence gates pass: all changed files are inside the task's allowed set, and tests are green

Gate failures cannot be overridden by the arbiter, even at confidence `0.99`. Anything else escalates to
`HUMAN_REQUIRED`. Repeated `REWORK` beyond `max_rework` (default 3) escalates as well.

## Installation

```bash
git clone https://github.com/<you>/metacore-hub && cd metacore-hub
pip install -e ".[dev]"
pytest -q
python examples/demo.py
```

Requires Python 3.10+. Optional Gemini support: `pip install -e ".[gemini]"`.

## Quick start

```python
from metacore import CoreEngine, EnginePolicy, RuleBasedArbiter
from metacore.orchestrator import arbitrate
from metacore.worker import MockWorker

engine = CoreEngine(MockWorker(), EnginePolicy(auto_merge_threshold=0.85, max_rework=3))

task_id = engine.dispatch_task(
    repo="acme/service",
    branch="feat/retry-logic",          # main/master are rejected by the contract
    prompt="Add exponential backoff to the HTTP client",
    files=["client.py"],                # the only files the worker may touch
)

engine.handle_event({"type": "worker_started", "task_id": task_id})
engine.handle_event({
    "type": "patch_ready", "task_id": task_id,
    "diff_files": ["client.py"],
    "tests": {"passed": 12, "failed": 0},
})

print(arbitrate(engine, RuleBasedArbiter(), task_id))   # TaskState.MERGED
assert engine.audit.verify()
```

## Event contracts

All inbound events are validated against JSON Schema (Draft 2020-12) in `metacore/contracts.py`.

| Event | Producer | Required fields |
|---|---|---|
| `worker_started`, `worker_activity`, `worker_failed`, `cancel` | worker | `type`, `task_id` |
| `patch_ready` | worker | `task_id`, `diff_files[]`, `tests{passed,failed}` |
| `arbiter_verdict` | arbiter | `task_id`, `verdict` (`APPROVE` \| `REWORK` \| `ESCALATE_HUMAN`), `confidence` ∈ [0,1]; `rework_instructions` when `REWORK` |
| `human_decision` | operator | `task_id`, `decision` (`APPROVE` \| `REWORK` \| `CANCEL`) |

Invalid or misrouted events never touch state; they are appended to `engine.dlq`.

## Modules

| Module | Responsibility |
|---|---|
| `metacore.engine` | `CoreEngine`: dispatch, event handling, policy, merge gate |
| `metacore.fsm` | States and the allowed-transition table |
| `metacore.contracts` | JSON Schema contracts and validation |
| `metacore.arbiter` | `RuleBasedArbiter` (offline, deterministic), `GeminiArbiter` (LLM with safe fallback) |
| `metacore.watchdog` | Stall detection, tiered nudges, circuit breaker |
| `metacore.audit` | Append-only hash-chained log with `verify()` |
| `metacore.a2a` | A2A card promotion (0.3 → 1.0), SSRF allowlist, fail-fast extension check |
| `metacore.orchestrator` | `arbitrate()` glue: arbiter proposes, engine disposes |

## Extending

**Custom worker** (Jules, a CI runner, anything): implement three methods.

```python
class MyWorker:
    def start(self, task_id: str, payload: dict) -> None: ...
    def rework(self, task_id: str, instructions: str) -> None: ...
    def nudge(self, task_id: str, level: int) -> None: ...   # 1 = continue, 2 = diagnostic
```

**Gemini arbiter:**

```python
from metacore import GeminiArbiter
arbiter = GeminiArbiter.from_google_genai(model="gemini-2.5-pro")   # reads GEMINI_API_KEY
```

If the model returns malformed JSON, violates the schema, or the call fails, the arbiter falls back to deterministic
rules and caps confidence at `0.8`, so a human confirms the merge.

## Security notes

- **No silent degradation.** `a2a.require_extension_confirmed()` raises if a proxy stripped `X-A2A-Extensions` and the
  server fell back to the legacy executor.
- **SSRF.** `a2a.fetch_card()` enforces a host allowlist and blocks link-local/metadata addresses.
- **Tamper evidence.** Audit entries are chained with SHA-256; editing history breaks `verify()`.
- **Scope pinning.** Workers are bound to an explicit file list per task.

## Roadmap

- [ ] ADK integration: `RemoteA2aAgent(use_legacy=False)` transport
- [ ] Confidence calibration (Platt scaling, Brier score) for the 0.85 threshold
- [ ] Persistent task store and event bus
- [ ] Prompt-as-code versioning and canary rollout hooks

## Contributing

Issues and PRs welcome. Run `pytest -q` before submitting; new behavior needs a test.

## License

MIT
