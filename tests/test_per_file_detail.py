from intelligence_format import format_grounded_context


def test_context_renders_per_file_rows():
    ctx = {
        "case": {"id": 3812, "name": "Test Files", "analysis_status": "completed"},
        "run": {"run_id": 21500},
        "inspections": {
            "__TOTAL__": {"total": 2, "inspected_complete": 2, "hash_failed": 0,
                          "with_text": 2, "with_entities": 2, "importance_avg": 0.8},
            "__FILES__": [
                {"latest_path": "/Private Share/Test Files/CPI Funding Status.pdf",
                 "category": "02_Documents", "subcategory": "Reports",
                 "file_purpose": "funding status report", "importance_score": 0.88,
                 "primary_entities": ["ITC Ventures LLC", "Nicholas Mitchell"],
                 "extracted_text_sample": "2893 Executive Park Drive..."},
            ],
        },
    }
    out = format_grounded_context(ctx)
    assert "CPI Funding Status.pdf" in out
    assert "Nicholas Mitchell" in out
    assert "[F1]" in out  # per-file source tag


def test_files_sentinel_not_rendered_as_category():
    ctx = {
        "case": {"id": 1, "name": "C", "analysis_status": "completed"},
        "run": {"run_id": 9},
        "inspections": {
            "02_Documents": {"total": 1, "inspected_complete": 1, "hash_failed": 0,
                             "with_text": 1, "with_entities": 1, "importance_avg": 0.5},
            "__TOTAL__": {"total": 1, "inspected_complete": 1, "hash_failed": 0,
                          "with_text": 1, "with_entities": 1, "importance_avg": 0.5},
            "__FILES__": [{"latest_path": "/x/y.pdf", "category": "02_Documents",
                           "subcategory": "", "file_purpose": "p", "importance_score": 0.5,
                           "primary_entities": [], "extracted_text_sample": ""}],
        },
    }
    out = format_grounded_context(ctx)
    assert "[INS:__FILES__]" not in out      # not treated as a category
    assert "y.pdf" in out                    # but IS rendered in the files block
