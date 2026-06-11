"""Guard: no agency-blind Cases-API reads in resolver/panel modules.

Every ``queries.<read_fn>(...)`` call in the files below must carry an
explicit ``agency_id=`` kwarg so the X-Imperal-Agency-ID header reaches
the Cases API on every read path (Track C, sharelock-C3).
"""
import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_QUERY_FNS = ("get_cases", "get_analysis", "get_files", "list_runs",
              "get_latest_active_run", "sign_report_url", "list_gaps",
              "get_run", "get_graph")


def _src(name):
    with open(os.path.join(_ROOT, name)) as f:
        return f.read()


def test_no_agency_blind_queries_calls():
    offenders = []
    # handlers.py joined the list after C0's "handlers already pass agency"
    # premise proved false for one site (bare get_cases in _load_case_summary).
    for fname in ("case_resolver.py", "panels.py", "panels_case.py",
                  "panels_gap_review.py", "panels_graph.py", "panels_analysis.py",
                  "handlers.py"):
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
