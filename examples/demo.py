"""python examples/demo.py -> runs one task through rework, escalation and merge."""
from metacore import CoreEngine, EnginePolicy, RuleBasedArbiter
from metacore.orchestrator import arbitrate
from metacore.worker import MockWorker

e = CoreEngine(MockWorker(), EnginePolicy(), merger=lambda t: print("  MERGED", t.id))
tid = e.dispatch_task("Sega757/adk-go", "feat/event-routes", "add Event.Routes", ["event.go"])
e.handle_event({"type": "worker_started", "task_id": tid})

print("attempt 1: failing tests")
e.handle_event({"type": "patch_ready", "task_id": tid, "diff_files": ["event.go"],
                "tests": {"passed": 3, "failed": 1, "log": "FAIL TestRoutes"}})
print("  ->", arbitrate(e, RuleBasedArbiter(), tid).value)

print("attempt 2: green")
e.handle_event({"type": "patch_ready", "task_id": tid, "diff_files": ["event.go"],
                "tests": {"passed": 4, "failed": 0}})
print("  ->", arbitrate(e, RuleBasedArbiter(), tid).value)
print("audit chain valid:", e.audit.verify(), "| entries:", len(e.audit.entries))
