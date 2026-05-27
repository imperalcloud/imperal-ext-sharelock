"""Federal v5-3: every read handler MUST have a Pydantic return model in models.py."""
import importlib


def test_models_module_imports():
    m = importlib.import_module("models")
    assert hasattr(m, "CaseRecord"), "CaseRecord must be defined in models.py"
    assert hasattr(m, "DocSearchHit"), "DocSearchHit must be defined in models.py"


def test_case_record_has_required_fields():
    from models import CaseRecord
    required = {"id", "name"}  # minimum overlap with CreateCaseParams + list_cases dicts
    fields = set(CaseRecord.model_fields.keys())
    assert required.issubset(fields), f"CaseRecord missing fields {required - fields}"
