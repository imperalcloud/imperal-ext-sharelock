"""Guard: no agency-blind Cases-API reads in resolver/panel modules.

Every ``queries.<read_fn>(...)`` call in the files below must carry an
explicit ``agency_id=`` kwarg so the X-Imperal-Agency-ID header reaches
the Cases API on every read path (Track C, sharelock-C3).
"""
import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# FULL Cases-API read surface (+ share writes) — every fn that accepts an
# agency_id kwarg. get_unlock / get_agency_storage / put_agency_storage are
# excluded by design: they take the id positionally (unlock is keyed by
# imperal_id; agency settings are keyed by agency_id itself).
_QUERY_FNS = ("get_cases", "get_analysis", "get_files", "list_runs",
              "get_latest_active_run", "sign_report_url", "list_gaps",
              "get_run", "get_graph", "get_case", "get_shares",
              "post_share", "delete_share",
              "get_taxonomy", "get_audit_log", "list_summaries",
              "list_entities", "list_inspections")


def _src(name):
    with open(os.path.join(_ROOT, name)) as f:
        return f.read()


def test_no_agency_blind_queries_calls():
    offenders = []
    # handlers.py joined the list after C0's "handlers already pass agency"
    # premise proved false for one site (bare get_cases in _load_case_summary).
    for fname in ("case_resolver.py", "panels.py", "panels_case.py",
                  "panels_case_tabs.py",
                  "panels_gap_review.py", "panels_graph.py", "panels_analysis.py",
                  "panels_share.py",
                  "handlers.py", "handlers_share.py", "handlers_files.py",
                  "handlers_analysis.py", "intelligence_context.py",
                  "skeleton.py", "queries_collab.py", "queries_analysis.py"):
        src = _src(fname)
        for fn in _QUERY_FNS:
            for m in re.finditer(r"queries\." + fn + r"\(", src):
                # the full call expression from the open paren
                depth, i = 0, m.end() - 1
                while i < len(src):
                    if src[i] == "(":
                        depth += 1
                    elif src[i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                call = src[m.start():i + 1]
                if "agency_id" not in call:
                    offenders.append(f"{fname}: {call[:80]}")
    assert not offenders, "agency-blind queries calls:\n" + "\n".join(offenders)
