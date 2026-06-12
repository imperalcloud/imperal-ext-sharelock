"""Rule-6 split of queries.py (2026-06-12) — compatibility + no-circular proof.

Layout: queries_http (leaf transport) ← queries / queries_collab /
queries_analysis. ``queries`` re-exports EVERYTHING so all existing
``queries.<fn>`` call sites and test monkeypatching keep working; the leaf
transport means there is no circular import under ANY import order.
"""
import os
import subprocess
import sys

import queries
import queries_analysis
import queries_collab
import queries_http

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# The full public surface queries.py exposed before the split (minus the
# dead get_members, removed 2026-06-12 — zero call sites).
_SURFACE = (
    "get_cases", "get_case", "create_case", "get_files",
    "get_analysis", "start_analysis", "cancel_analysis", "sign_report_url",
    "list_runs", "get_run", "get_latest_active_run", "list_gaps",
    "post_gap_decision", "get_graph", "get_taxonomy", "list_summaries",
    "list_entities", "list_inspections", "get_audit_log",
    "post_share", "delete_share", "get_shares",
    "get_unlock", "get_agency_storage", "put_agency_storage",
    "CasesAPIError", "_get", "_post", "_put", "_delete",
    "_hdrs", "_raise_for_error",
)


def test_full_surface_still_importable_via_queries():
    missing = [n for n in _SURFACE if not hasattr(queries, n)]
    assert not missing, f"queries.<fn> compatibility broken: {missing}"


def test_reexports_are_the_same_objects():
    """Monkeypatching/`except CasesAPIError` rely on object identity."""
    assert queries.post_share is queries_collab.post_share
    assert queries.get_unlock is queries_collab.get_unlock
    assert queries.get_agency_storage is queries_collab.get_agency_storage
    assert queries.list_runs is queries_analysis.list_runs
    assert queries.get_latest_active_run is queries_analysis.get_latest_active_run
    assert queries.list_inspections is queries_analysis.list_inspections
    assert queries.CasesAPIError is queries_http.CasesAPIError
    assert queries._get is queries_http._get


def test_direct_import_no_circular_crash_any_order():
    """Fresh interpreter, ADVERSARIAL order: the split submodules first,
    then queries — must not raise (leaf-transport design)."""
    code = (
        "import sys; sys.path.insert(0, {root!r}); "
        "import queries_collab, queries_analysis, queries_http, queries; "
        "assert queries.post_share is queries_collab.post_share; "
        "assert queries.list_runs is queries_analysis.list_runs; "
        "print('IMPORT-OK')"
    ).format(root=_ROOT)
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "IMPORT-OK" in r.stdout, (
        f"circular/dependency crash on direct import:\n{r.stderr}")


def test_dead_get_members_removed():
    assert not hasattr(queries, "get_members"), \
        "get_members was dead code (zero call sites) — must stay deleted"
