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
    """CaseListResponse.cases is typed list[CaseRecord]."""
    from models import CaseListResponse, CaseRecord
    fields = CaseListResponse.model_fields
    assert "cases" in fields
    assert "count" in fields
    # Verify the list element type is CaseRecord
    assert fields["cases"].annotation == list[CaseRecord], (
        f"CaseListResponse.cases must be list[CaseRecord], got {fields['cases'].annotation}"
    )


def test_gap_review_envelope_shape():
    """GapReviewResponse mirrors handlers_analysis.py:152-161 data dict."""
    from models import GapReviewResponse
    required = {"case_id", "run_id", "gaps", "by_severity",
                "confidence_current", "confidence_potential"}
    fields = set(GapReviewResponse.model_fields.keys())
    assert required.issubset(fields), f"GapReviewResponse missing fields {required - fields}"
