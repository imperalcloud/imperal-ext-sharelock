from intelligence_format import render_findings_deterministic


def _ctx_with_indictment():
    return {
        "summaries": [
            {"category": "_indictment", "summary_json": {
                "case_theory": "Nicholas Mitchell, acting through ITC Ventures LLC, devised a scheme to defraud Manuel Serrano.",
                "target_subjects": [
                    {"name": "Nicholas Mitchell", "role": "principal", "evidence_summary": "controlled ITC accounts"},
                    {"name": "Manuel Serrano", "role": "victim", "evidence_summary": "defrauded investor"},
                ],
                "candidate_charges": [
                    {"charge_code": "18 U.S.C. 1343", "charge_title": "Wire Fraud", "target_subject": "Nicholas Mitchell"},
                ],
            }},
            {"category": "_cross_cutting", "summary_json": {"narrative_synthesis": "Multi-phase escrow fraud."}},
            {"category": "02_Documents", "summary_json": {"executive_summary": "36 legal/financial files."}},
        ],
    }


def test_render_contains_defendant_and_charge():
    out = render_findings_deterministic(_ctx_with_indictment())
    assert "Nicholas Mitchell" in out
    assert "Wire Fraud" in out
    assert "escrow fraud" in out
    assert "36 legal" in out


def test_render_empty_when_no_findings():
    assert render_findings_deterministic({"summaries": []}) == ""
    assert render_findings_deterministic({"error": "x"}) == ""


def test_render_empty_when_indictment_fields_blank():
    out = render_findings_deterministic({"summaries": [{"category": "_indictment", "summary_json": {}}]})
    assert out == ""


def test_render_surfaces_merit_only():
    out = render_findings_deterministic({"summaries": [
        {"category": "_indictment", "summary_json": {"prosecutive_merit_overall": "STRONG"}},
    ]})
    assert "STRONG" in out
