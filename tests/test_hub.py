import pytest
from metacore import CoreEngine, EnginePolicy, TaskState, RuleBasedArbiter, GeminiArbiter, Watchdog
from metacore.contracts import ContractViolation
from metacore.fsm import IllegalTransition, check
from metacore.orchestrator import arbitrate
from metacore.worker import MockWorker
from metacore import a2a


class Clock:
    t = 0.0
    def __call__(self): return self.t


def mk(**pol):
    w, c = MockWorker(), Clock()
    merged = []
    e = CoreEngine(w, EnginePolicy(**pol), merger=lambda t: merged.append(t.id), clock=c)
    return e, w, c, merged


def patch(tid, files=("a.py",), passed=5, failed=0, diff="x"):
    return {"type": "patch_ready", "task_id": tid, "diff_files": list(files), "diff": diff,
            "tests": {"passed": passed, "failed": failed, "log": "log"}}


def new(e): return e.dispatch_task("repo", "feat/x", "do it", ["a.py"])


def test_happy_path_auto_merge():
    e, w, c, merged = mk()
    tid = new(e)
    e.handle_event({"type": "worker_started", "task_id": tid})
    e.handle_event(patch(tid))
    assert arbitrate(e, RuleBasedArbiter(), tid) == TaskState.MERGED
    assert merged == [tid] and e.audit.verify()


def test_main_branch_rejected():
    e, *_ = mk()
    with pytest.raises(ContractViolation):
        e.dispatch_task("r", "main", "p", ["a.py"])


def test_failing_tests_rework_then_limit_escalates():
    e, w, *_ = mk(max_rework=2)
    tid = new(e)
    for i in range(3):
        e.handle_event(patch(tid, failed=1))
        st = arbitrate(e, RuleBasedArbiter(), tid)
    assert st == TaskState.HUMAN_REQUIRED and len(w.reworks) == 2


def test_llm_cannot_waive_evidence_gates():
    e, *_ = mk()
    tid = new(e)
    e.handle_event(patch(tid, files=("a.py", "secrets.env"), failed=0))
    st = e.handle_event({"type": "arbiter_verdict", "task_id": tid, "verdict": "APPROVE", "confidence": 0.99})
    assert st == TaskState.HUMAN_REQUIRED  # out-of-scope file, despite 0.99


def test_low_confidence_and_hitl_go_to_human():
    e, *_ = mk()
    tid = new(e); e.handle_event(patch(tid))
    assert e.handle_event({"type": "arbiter_verdict", "task_id": tid, "verdict": "APPROVE",
                           "confidence": 0.84}) == TaskState.HUMAN_REQUIRED
    e2, *_ = mk(hitl_mode=True)
    t2 = new(e2); e2.handle_event(patch(t2))
    assert e2.handle_event({"type": "arbiter_verdict", "task_id": t2, "verdict": "APPROVE",
                            "confidence": 1.0}) == TaskState.HUMAN_REQUIRED


def test_human_resolves():
    e, w, c, merged = mk(hitl_mode=True)
    tid = new(e); e.handle_event(patch(tid)); arbitrate(e, RuleBasedArbiter(), tid)
    assert e.handle_event({"type": "human_decision", "task_id": tid, "decision": "APPROVE"}) == TaskState.MERGED


def test_invalid_events_go_to_dlq_state_untouched():
    e, *_ = mk()
    tid = new(e)
    assert e.handle_event({"type": "arbiter_verdict", "task_id": tid, "verdict": "REWORK",
                           "confidence": 0.9}) is None  # REWORK w/o instructions
    assert e.handle_event({"type": "patch_ready", "task_id": tid}) is None
    assert e.handle_event({"type": "worker_started", "task_id": "nope"}) is None
    assert e.tasks[tid].state == TaskState.DISPATCHED and len(e.dlq) == 3


def test_verdict_in_wrong_state_dlq():
    e, *_ = mk()
    tid = new(e)
    e.handle_event({"type": "arbiter_verdict", "task_id": tid, "verdict": "APPROVE", "confidence": 1})
    assert e.tasks[tid].state == TaskState.DISPATCHED and e.dlq


def test_fsm_illegal_transition():
    with pytest.raises(IllegalTransition):
        check(TaskState.MERGED, TaskState.IN_PROGRESS)


def test_watchdog_nudges_then_breaker():
    e, w, c, _ = mk()
    wd = Watchdog(e, stall_seconds=120, max_nudges=3)
    tid = new(e); e.handle_event({"type": "worker_started", "task_id": tid})
    acts = []
    for _ in range(3):
        c.t += 121
        acts += wd.tick()
    assert [a for _, a in acts] == ["nudge1", "nudge2", "breaker"]
    assert e.tasks[tid].state == TaskState.HUMAN_REQUIRED and w.nudges == [(tid, 1), (tid, 2)]


def test_watchdog_activity_resets():
    e, w, c, _ = mk()
    wd = Watchdog(e)
    tid = new(e); e.handle_event({"type": "worker_started", "task_id": tid})
    c.t += 121; wd.tick()
    e.handle_event({"type": "worker_activity", "task_id": tid})
    c.t += 60
    assert wd.tick() == []


def test_audit_tamper_detected():
    e, *_ = mk(); new(e)
    e.handle_event({"type": "worker_started", "task_id": "task-1"})
    e.audit.entries[0]["payload"] = {"evil": 1}
    assert not e.audit.verify()


def test_gemini_arbiter_valid_and_fallback():
    ok = GeminiArbiter(lambda p: '```json\n{"verdict":"APPROVE","confidence":0.9,"reasons":["ok"]}\n```')
    assert ok.review("task-1", patch("task-1"), ["a.py"])["verdict"] == "APPROVE"
    bad = GeminiArbiter(lambda p: "not json at all")
    v = bad.review("task-1", patch("task-1"), ["a.py"])
    assert v["verdict"] == "APPROVE" and v["confidence"] <= 0.8  # fallback is forced through human gate
    e, *_ = mk(); tid = new(e); e.handle_event(patch(tid))
    assert e.handle_event(v) == TaskState.HUMAN_REQUIRED


def test_a2a_promote_and_failfast():
    c = a2a.promote_card({"name": "x", "url": "http://h:1", "preferredTransport": "JSONRPC"})
    assert c["supportedInterfaces"] == [{"protocolBinding": "JSONRPC", "url": "http://h:1"}]
    with pytest.raises(a2a.A2AError):
        a2a.require_extension_confirmed({})  # silent fallback
    a2a.require_extension_confirmed({"activated_extensions": [a2a.EXT_URI]})
    assert a2a.request_headers() == {"X-A2A-Extensions": a2a.EXT_URI}


def test_a2a_ssrf_blocked():
    with pytest.raises(a2a.A2AError):
        a2a.check_url("http://169.254.169.254/latest", {"agent.example.com"})
    with pytest.raises(a2a.A2AError):
        a2a.check_url("file:///etc/passwd", {"x"})
