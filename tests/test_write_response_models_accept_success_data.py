"""Write-handler data_model= envelopes must accept the exact success `data`
dicts the handlers actually produce (model ↔ return contract).

Each construction below mirrors an `ActionResult.success(data={...})` call in
handlers.py / handlers_analysis.py. This guards against model drift: if a
handler's success shape changes (or a model field is typed too narrowly), the
runtime data_model validation would emit warn-only noise in production — these
tests turn that into a hard local failure.

Requires the SDK (models.py imports imperal_sdk.sdl). conftest puts the ext
root on sys.path.
"""
from models import (
    CreateCaseResponse, SyncCasesResponse, RunAnalysisResponse,
    CancelAnalysisResponse, GapDecisionResponse,
)


def test_create_case_response_accepts_success_data():
    # handlers.py fn_create_case: data={"case_id", "name"}
    CreateCaseResponse(case_id=3812, name="Test Files")
    CreateCaseResponse(case_id="?", name="Test Files")  # API-omitted-id fallback


def test_sync_cases_response_accepts_success_data():
    # handlers.py fn_sync_cases: data={"created", "skipped", "total_folders"}
    SyncCasesResponse(created=["Alpha"], skipped=["Beta"], total_folders=2)
    SyncCasesResponse(created=[], skipped=[], total_folders=0)


def test_run_analysis_response_accepts_success_data():
    # handlers_analysis.py fn_run_analysis: data={"case_id","status","run_id","version"}
    RunAnalysisResponse(case_id=1, status="started", run_id=42, version=3)
    RunAnalysisResponse(case_id=1, status="started", run_id=None, version=None)
    # run_id/version pass through from the Cases API unmodified; the sibling
    # cancel_analysis handler already models run_id as int|str, so a string
    # run id must not be rejected here either (consistency + warn-only safety).
    RunAnalysisResponse(case_id=1, status="started", run_id="run-abc-1", version="v2")


def test_cancel_analysis_response_accepts_success_data():
    # handlers_analysis.py fn_cancel_analysis: data={"case_id","run_id","status"}
    CancelAnalysisResponse(case_id=1, run_id=42, status="cancelled")
    CancelAnalysisResponse(case_id=1, run_id="?", status="cancelled")  # sentinel fallback


def test_gap_decision_response_accepts_both_handlers():
    # continue_analysis (decision="continue") + resume_with_new_evidence
    # (decision="add_evidence") share this envelope: data={"case_id","run_id","decision"}
    GapDecisionResponse(case_id=1, run_id=42, decision="continue")
    GapDecisionResponse(case_id=1, run_id=42, decision="add_evidence")
    GapDecisionResponse(case_id=1, run_id=None, decision="continue")
