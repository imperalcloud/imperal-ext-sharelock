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
    ShareCaseResponse, UnshareCaseResponse, UploadReceipt,
    SaveSettingsResponse,
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


def test_share_case_response_accepts_success_data():
    # handlers_share.py fn_share_case: data={"shared","case_id","imperal_id","note"}
    ShareCaseResponse(shared=True, case_id=5, imperal_id="imp_u_x", note=None)
    ShareCaseResponse(shared=True, case_id=5, imperal_id="bob@example.com",
                      note="identifier does not look like an imperal id "
                           "(imp_u_...) — stored verbatim; email lookup "
                           "lands server-side later")


def test_unshare_case_response_accepts_success_data():
    # handlers_share.py fn_unshare_case: data={"unshared","deleted","case_id","imperal_id"}
    UnshareCaseResponse(unshared=True, deleted=1, case_id=5, imperal_id="imp_u_x")
    UnshareCaseResponse(unshared=False, deleted=0, case_id=5, imperal_id="imp_u_x")


def test_upload_receipt_accepts_success_and_limit_data():
    # handlers_files.py success: data={"uploaded","case_id","case_name","files","failed","note"}
    UploadReceipt(uploaded=2, case_id=7, case_name="CaseU",
                  files=["a.txt", "b.txt"], failed=[],
                  note="analysis will pick the new files up on the next census run")
    # limit fact: data={"uploaded": 0, "case_id", "files": [], "reason"}
    UploadReceipt(uploaded=0, case_id=7, files=[],
                  reason="limit 8 files per upload (got 9)")


def test_save_settings_response_accepts_success_and_denial_data():
    # handlers_admin.py success (MASKED — booleans only for secrets)
    SaveSettingsResponse(saved=True, agency_id="acme",
                         storage_url="https://nc.example.org",
                         storage_username="u", storage_base_path="/Sharelock/",
                         storage_password_set=True, database_configured=False)
    # typed denial fact
    SaveSettingsResponse(saved=False, agency_id="acme",
                         reason="admin_role_required")
