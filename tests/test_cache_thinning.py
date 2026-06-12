"""I-CACHE-VALUE-SIZE-CAP-64KB — shared files[] trim before CaseSummary caching.

Live incident 2026-06-12: the Analysis tab on «Alex Case 1» (2655 files)
raised «cache value envelope too large: 142716 > 65536» because the panel
copy of the summary loader lacked the trim handlers.py had. The trim is now
ONE shared helper used by both.
"""
import os
import re

from cache_models import _MAX_CACHED_FILES, thin_case_summary_data

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_big_files_list_capped_and_counted():
    data = {"files": [{"filename": f"f{i}.pdf", "size": i} for i in range(2655)]}
    out = thin_case_summary_data(data)
    assert len(out["files"]) == _MAX_CACHED_FILES
    assert out["file_count"] == 2655


def test_small_list_untouched_and_existing_count_kept():
    data = {"files": [{"filename": "a"}], "file_count": 7}
    out = thin_case_summary_data(data)
    assert len(out["files"]) == 1 and out["file_count"] == 7
    assert thin_case_summary_data({}) == {}


def test_both_summary_loaders_use_the_shared_trim():
    # panel summary loader moved to panels_case_tabs.py (Rule-6 split).
    for fname in ("handlers.py", "panels_case_tabs.py"):
        with open(os.path.join(_ROOT, fname)) as f:
            src = f.read()
        assert "thin_case_summary_data" in src, f"{fname} must use the shared trim"
        assert not re.search(r'data\["files"\]\[:20\]', src), \
            f"{fname}: inline trim must not reappear (use the helper)"
