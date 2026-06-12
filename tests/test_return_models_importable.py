"""Federal v5-3: every read handler MUST have a Pydantic return model in models.py."""
import importlib


def test_models_module_imports():
    m = importlib.import_module("models")
    # Row types (nested under envelopes — per admin precedent)
    assert hasattr(m, "CaseRecord"), "CaseRecord row type must be defined"
    assert hasattr(m, "DocSearchHit"), "DocSearchHit row type must be defined"
    assert hasattr(m, "GapReviewItem"), "GapReviewItem row type must be defined"
    # Envelopes (the actual data_model= targets in Task 4)
    assert hasattr(m, "CaseListResponse"), "CaseListResponse envelope must be defined"
    assert hasattr(m, "DocSearchResponse"), "DocSearchResponse envelope must be defined"
    assert hasattr(m, "GapReviewResponse"), "GapReviewResponse envelope must be defined"
    assert hasattr(m, "CaseChatResponse"), "CaseChatResponse envelope must be defined"


def test_case_record_fields_match_params_symmetry():
    """V23 field-name symmetry: input Params + return data_model share field names where overlap exists."""
    from models import CaseRecord
    required = {"id", "name"}
    fields = set(CaseRecord.model_fields.keys())
    assert required.issubset(fields), f"CaseRecord missing fields {required - fields}"


def test_case_list_envelope_contains_case_rows():
    """CaseListResponse is a real sdl.EntityList[CaseRecord] — rows live in items=[...]; count kept as an additive scalar (SDL migration 2026-06-02, no legacy {cases:[dict]} wrapper)."""
    from models import CaseListResponse, CaseRecord
    fields = CaseListResponse.model_fields
    assert "items" in fields
    assert "count" in fields
    # Verify the list element type is CaseRecord (EntityList[CaseRecord])
    assert fields["items"].annotation == list[CaseRecord], (
        f"CaseListResponse.items must be list[CaseRecord], got {fields['items'].annotation}"
    )


def test_case_share_list_envelope_contains_share_rows():
    """CaseShareListResponse is a real sdl.EntityList[CaseShareRecord] —
    grant rows live in items=[...]; case_id + owner are additive scalars."""
    from models import CaseShareListResponse, CaseShareRecord
    fields = CaseShareListResponse.model_fields
    assert "items" in fields and "case_id" in fields and "owner" in fields
    assert fields["items"].annotation == list[CaseShareRecord], (
        f"CaseShareListResponse.items must be list[CaseShareRecord], "
        f"got {fields['items'].annotation}"
    )
    # Raw Cases API rows ride through — canonical id/title derive from
    # imperal_id/email via the mode='before' validator.
    rec = CaseShareRecord(imperal_id="imp_u_a", email="a@x.com",
                          granted_by="imp_u_o", created_at="2026-06-12")
    assert rec.id == "imp_u_a" and rec.title == "a@x.com"
    assert rec.kind == "case_share"


def test_gap_review_envelope_shape():
    """GapReviewResponse is a real sdl.EntityList[GapReviewItem] — gap rows live in items=[...]; the handler's scalars (case_id/run_id/by_severity/confidence_*) are kept as additive typed fields (SDL migration 2026-06-02; legacy 'gaps' list is now 'items')."""
    from models import GapReviewResponse
    required = {"case_id", "run_id", "items", "by_severity",
                "confidence_current", "confidence_potential"}
    fields = set(GapReviewResponse.model_fields.keys())
    assert required.issubset(fields), f"GapReviewResponse missing fields {required - fields}"
