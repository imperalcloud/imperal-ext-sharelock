"""Elder-friendly proactive-communication batch (DOJ users).

Webbee must SAY what's happening, CONFIRM re-runs of already-completed
cases, and never hide a needed decision behind a silent button.

D1 — run_analysis: confirm re-run of an already-completed case + never
     print "run #None" on a fresh start.
D2 — chat: GAP_REVIEW state surfaces the pending decision (continue /
     add evidence) so the user can answer in chat, not only the panel.
D3 — skeleton: surface + PUSH the gap-review wait (ctx.notify on the
     TRANSITION into gap_review, not every poll).

Federal contract: tools emit FACTS (action discriminator + scalars), the
narrator owns language (ICNLI); ctx.notify is guarded so a notify failure
never breaks the skeleton.
"""
import asyncio

import pytest


# ── shared fakes ──────────────────────────────────────────────────────────────


class _User:
    imperal_id = "imp_u_test"
    agency_id = "default"
    email = "u@example.org"


class _Ctx:
    def __init__(self):
        self.user = _User()
        self.cache = None


def _unlock(monkeypatch):
    import auth_gate
    from auth_gate import UnlockState

    async def fake(ctx, force_fresh=False):
        return UnlockState(unlocked=True, agency_id="default", role="admin")
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


# ── D1: run_analysis re-run confirmation ────────────────────────────────────────


def test_run_analysis_completed_no_confirm_asks_rerun(monkeypatch):
    """Completed case + no confirm → action=confirm_rerun, start NOT called."""
    import handlers_analysis
    import queries
    _unlock(monkeypatch)
    started = {"called": False}

    async def analysis(case_id, agency_id=None):
        return {"analysis_status": "completed",
                "analysis_version": 4,
                "analysis_updated_at": "2026-06-10T12:00:00Z"}

    async def runs(case_id, agency_id=None):
        return [{"version": 4, "status": "completed", "run_id": 21500}]

    async def start(case_id, user_id, agency_id=None):
        started["called"] = True
        return {"version": 5, "workflow_id": "wf-x", "analysis_status": "pending"}

    monkeypatch.setattr(queries, "get_analysis", analysis)
    monkeypatch.setattr(queries, "list_runs", runs)
    monkeypatch.setattr(queries, "start_analysis", start)

    res = asyncio.run(handlers_analysis.fn_run_analysis(
        _Ctx(), handlers_analysis.RunAnalysisParams(case_id=7)))
    assert res.status == "success"
    assert res.data["action"] == "confirm_rerun"
    assert res.data["already_version"] == 4
    assert started["called"] is False, "must NOT start without confirm"
    assert "re-run" in res.summary.lower()


def test_run_analysis_completed_with_confirm_starts(monkeypatch):
    """Completed case + confirm=True → start IS called."""
    import handlers_analysis
    import queries
    _unlock(monkeypatch)
    started = {"called": False}

    async def analysis(case_id, agency_id=None):
        return {"analysis_status": "completed", "analysis_version": 4}

    async def runs(case_id, agency_id=None):
        return [{"version": 4, "status": "completed"}]

    async def start(case_id, user_id, agency_id=None):
        started["called"] = True
        return {"version": 5, "workflow_id": "wf-y", "analysis_status": "pending"}

    monkeypatch.setattr(queries, "get_analysis", analysis)
    monkeypatch.setattr(queries, "list_runs", runs)
    monkeypatch.setattr(queries, "start_analysis", start)

    res = asyncio.run(handlers_analysis.fn_run_analysis(
        _Ctx(), handlers_analysis.RunAnalysisParams(case_id=7, confirm=True)))
    assert res.status == "success"
    assert started["called"] is True, "confirm=True must start a new run"
    assert res.data.get("status") == "started"


def test_run_analysis_gap_review_does_not_start(monkeypatch):
    """gap_review → awaiting_gap_decision, start NOT called."""
    import handlers_analysis
    import queries
    _unlock(monkeypatch)
    started = {"called": False}

    async def analysis(case_id, agency_id=None):
        return {"analysis_status": "gap_review", "analysis_version": 5}

    async def runs(case_id, agency_id=None):
        return [{"version": 5, "status": "gap_review"}]

    async def start(case_id, user_id, agency_id=None):
        started["called"] = True
        return {}

    monkeypatch.setattr(queries, "get_analysis", analysis)
    monkeypatch.setattr(queries, "list_runs", runs)
    monkeypatch.setattr(queries, "start_analysis", start)

    res = asyncio.run(handlers_analysis.fn_run_analysis(
        _Ctx(), handlers_analysis.RunAnalysisParams(case_id=7)))
    assert res.status == "success"
    assert res.data["action"] == "awaiting_gap_decision"
    assert started["called"] is False
    assert "gap review" in res.summary.lower()


def test_run_analysis_already_running_does_not_start(monkeypatch):
    """pending/running → already_running, start NOT called."""
    import handlers_analysis
    import queries
    _unlock(monkeypatch)
    started = {"called": False}

    async def analysis(case_id, agency_id=None):
        return {"analysis_status": "running", "analysis_version": 6}

    async def runs(case_id, agency_id=None):
        return [{"version": 6, "status": "running"}]

    async def start(case_id, user_id, agency_id=None):
        started["called"] = True
        return {}

    monkeypatch.setattr(queries, "get_analysis", analysis)
    monkeypatch.setattr(queries, "list_runs", runs)
    monkeypatch.setattr(queries, "start_analysis", start)

    res = asyncio.run(handlers_analysis.fn_run_analysis(
        _Ctx(), handlers_analysis.RunAnalysisParams(case_id=7)))
    assert res.status == "success"
    assert res.data["action"] == "already_running"
    assert started["called"] is False


def test_run_analysis_fresh_start_no_run_none(monkeypatch):
    """No prior analysis → start, and the summary must NOT print 'run #None'."""
    import handlers_analysis
    import queries
    _unlock(monkeypatch)
    started = {"called": False}

    async def analysis(case_id, agency_id=None):
        return {}  # no prior analysis

    async def runs(case_id, agency_id=None):
        return []

    async def start(case_id, user_id, agency_id=None):
        started["called"] = True
        return {"version": 1, "workflow_id": "wf-fresh", "analysis_status": "pending"}

    monkeypatch.setattr(queries, "get_analysis", analysis)
    monkeypatch.setattr(queries, "list_runs", runs)
    monkeypatch.setattr(queries, "start_analysis", start)

    res = asyncio.run(handlers_analysis.fn_run_analysis(
        _Ctx(), handlers_analysis.RunAnalysisParams(case_id=7)))
    assert res.status == "success"
    assert started["called"] is True
    assert "run #none" not in res.summary.lower(), "must not print run #None"
    assert res.data.get("version") == 1


# ── D2: chat GAP_REVIEW state ────────────────────────────────────────────────────


def test_resolve_state_gap_review():
    import chat
    assert chat.resolve_state(7, "gap_review") == "GAP_REVIEW"


def test_resolve_state_unaffected():
    import chat
    assert chat.resolve_state(7, "running") == "STATUS"
    assert chat.resolve_state(7, "completed") == "INTELLIGENCE"
    assert chat.resolve_state(7, None) == "INTAKE"
    assert chat.resolve_state(None, "completed") == "CASE_LIST"


def test_case_chat_gap_review_branch_presents_choices(monkeypatch):
    """GAP_REVIEW chat branch returns a fact mentioning continue + add evidence."""
    import handlers
    import queries
    _unlock(monkeypatch)

    async def fake_summary(ctx, user_id, case_id):
        from cache_models import CaseSummary
        return CaseSummary(active_case_id=7, case_name="Alex Case 1",
                           analysis_status="gap_review")

    async def fake_resolve(user_id, message, panel_case_id, skeleton_case_id,
                           history=None, agency_id=None):
        return 7, "name_match"

    async def runs(case_id, agency_id=None):
        return [{"version": 5, "status": "gap_review", "run_id": 900,
                 "confidence_current": 0.55, "confidence_potential": 0.82}]

    async def latest(case_id, agency_id=None):
        return (await runs(case_id, agency_id))[0]

    async def gaps(case_id, run_id=None, agency_id=None):
        return [{"id": 1, "severity": "BLOCKING", "description": "Missing bank records"},
                {"id": 2, "severity": "QUALITY", "description": "Unclear date"}]

    # handlers binds resolve_case_id / _load_case_summary at module level.
    monkeypatch.setattr(handlers, "_load_case_summary", fake_summary)
    monkeypatch.setattr(handlers, "resolve_case_id", fake_resolve)
    monkeypatch.setattr(queries, "list_runs", runs)
    monkeypatch.setattr(queries, "get_latest_active_run", latest)
    monkeypatch.setattr(queries, "list_gaps", gaps)

    res = asyncio.run(handlers.case_chat(
        _Ctx(), handlers.CaseChatParams(message="что там с делом Alex Case 1")))
    assert res.status == "success"
    assert res.data.get("state") == "gap_review"
    low = res.summary.lower()
    assert "continue" in low
    assert "add evidence" in low or "add more evidence" in low


# ── D3: skeleton gap-review surface + push ───────────────────────────────────────


def test_pick_active_case_prioritizes_gap_review():
    import skeleton
    enriched = [
        {"id": 1, "analysis_status": "completed"},
        {"id": 2, "analysis_status": "gap_review"},
        {"id": 3, "analysis_status": "running"},
    ]
    active = skeleton._pick_active_case(enriched)
    assert active["id"] == 2, "a paused (gap_review) case should be the active one"


def test_skeleton_alert_gap_review_transition_notifies():
    """Transition INTO gap_review → returns a string AND awaits ctx.notify once."""
    import skeleton

    calls = []

    class _NotifyCtx:
        async def notify(self, message, **kwargs):
            calls.append((message, kwargs))

    ctx = _NotifyCtx()
    old = {"analysis_status": "running", "case_name": "Alex Case 1"}
    new = {"analysis_status": "gap_review", "case_name": "Alex Case 1"}

    res = asyncio.run(skeleton.on_skeleton_alert(ctx, old=old, new=new))
    assert isinstance(res, str) and res, "must return a non-empty alert string"
    assert len(calls) == 1, "must push exactly one notify on the transition"
    msg = calls[0][0].lower()
    assert "decision" in msg or "continue" in msg


def test_skeleton_alert_gap_review_no_repeat_notify():
    """Same gap_review status on a later poll → no notify (transition-gated)."""
    import skeleton

    calls = []

    class _NotifyCtx:
        async def notify(self, message, **kwargs):
            calls.append(message)

    ctx = _NotifyCtx()
    old = {"analysis_status": "gap_review", "case_name": "Alex Case 1"}
    new = {"analysis_status": "gap_review", "case_name": "Alex Case 1"}

    asyncio.run(skeleton.on_skeleton_alert(ctx, old=old, new=new))
    assert calls == [], "must NOT re-notify when already in gap_review"


def test_skeleton_alert_notify_failure_never_breaks(monkeypatch):
    """A notify failure must never break the skeleton — still returns a string."""
    import skeleton

    class _BadCtx:
        async def notify(self, message, **kwargs):
            raise RuntimeError("gateway down")

    old = {"analysis_status": "running", "case_name": "Alex Case 1"}
    new = {"analysis_status": "gap_review", "case_name": "Alex Case 1"}

    res = asyncio.run(skeleton.on_skeleton_alert(_BadCtx(), old=old, new=new))
    assert isinstance(res, str) and res, "notify failure must not break alert"
