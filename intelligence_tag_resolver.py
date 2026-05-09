"""
Sharelock v2 — Tag → context-object resolver for intelligence_validator.

Pure dispatch by tag family. Returns the context object a given citation
refers to, or None if the tag is unknown. This module is the single source
of truth for what every citation family means; keep in lockstep with
intelligence_format.py (the renderer).
"""
from __future__ import annotations

from typing import Any


def _int_or_none(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _index_or_none(seq, idx: int | None) -> Any:
    if idx is None:
        return None
    i = idx - 1
    return seq[i] if 0 <= i < len(seq) else None


def _resolve_case(context: dict) -> Any:
    return context.get("case") or {}


def _resolve_run(context: dict) -> Any:
    return context.get("run") or {}


def _resolve_graph(context: dict) -> Any:
    return context.get("graph_stats") or {}


def _resolve_tax(qual1: str | None, qual2: str | None, context: dict) -> Any:
    taxonomy = context.get("taxonomy") or []
    if not qual1:
        return taxonomy
    for t in taxonomy:
        cat = t.get("category") or ""
        sub = t.get("subcategory") or ""
        if qual1 == cat and (not qual2 or qual2 == sub):
            return t
    if qual1 == "total_file_count_sum":
        return taxonomy
    return None


def _resolve_ins(qual1: str | None, qual2: str | None, context: dict) -> Any:
    inspections = context.get("inspections") or {}
    if not qual1:
        return inspections
    if qual1 in ("__TOTAL__", "error", "DEFINITION"):
        return inspections.get(qual1) or inspections
    key = f"{qual1}/{qual2}" if qual2 else qual1
    if key in inspections:
        return inspections[key]
    for k in inspections:
        if k == qual1 or k.startswith(f"{qual1}/"):
            return inspections[k]
    return None


def _resolve_gap(num: str, context: dict) -> Any:
    gaps = context.get("gaps") or []
    idx = _int_or_none(num)
    if idx is None:
        return gaps
    return _index_or_none(gaps, idx)


def _resolve_summary(num: str, subtype: str | None, subnum: str | None,
                     context: dict) -> Any:
    summs = [
        s for s in (context.get("summaries") or [])
        if not (s.get("category") or "").startswith("_")
    ]
    idx = _int_or_none(num)
    if idx is None:
        return summs
    s = _index_or_none(summs, idx)
    if s is None:
        return None
    if subtype == "F":
        findings = (s.get("summary_json") or {}).get("key_findings") or []
        return _index_or_none(findings, _int_or_none(subnum))
    return s


def _resolve_cross_cutting(num: str, subtype: str | None, subnum: str | None,
                           context: dict) -> Any:
    cc = next(
        (s for s in (context.get("summaries") or [])
         if s.get("category") == "_cross_cutting"),
        None,
    )
    if not cc:
        return None
    sj = cc.get("summary_json") or {}
    if num == "1":
        return sj.get("narrative_synthesis") or cc
    if num == "2":
        findings = sj.get("cross_cutting_findings") or []
        if subtype == "F":
            return _index_or_none(findings, _int_or_none(subnum))
        return findings
    if num == "3":
        contras = sj.get("contradictions_found") or []
        if subtype == "C":
            return _index_or_none(contras, _int_or_none(subnum))
        return contras
    if num == "4":
        return sj.get("confidence_assessment")
    return cc


def _resolve_indictment(num: str, subtype: str | None, subnum: str | None,
                        context: dict) -> Any:
    indict = next(
        (s for s in (context.get("summaries") or [])
         if s.get("category") == "_indictment"),
        None,
    )
    if not indict:
        return None
    sj = indict.get("summary_json") or {}
    if num == "1":
        return sj.get("case_theory")
    if num == "2":
        targets = sj.get("target_subjects") or []
        if subtype == "T":
            return _index_or_none(targets, _int_or_none(subnum))
        return targets
    if num == "3":
        charges = sj.get("candidate_charges") or []
        if subtype == "C":
            return _index_or_none(charges, _int_or_none(subnum))
        return charges
    if num == "4":
        return sj.get("brady_giglio_flags") or []
    if num == "5":
        return {
            "merit": sj.get("prosecutive_merit_overall"),
            "reasoning": sj.get("prosecutive_merit_reasoning"),
        }
    return indict


def _resolve_entity(num: str, context: dict) -> Any:
    ents = context.get("entities") or []
    idx = _int_or_none(num)
    if idx is None:
        return ents
    return _index_or_none(ents, idx)


def _resolve_audit(num: str, context: dict) -> Any:
    audit = context.get("audit") or []
    idx = _int_or_none(num)
    if idx is None:
        return audit
    return _index_or_none(audit, idx)


_DISPATCH = {
    "CASE":  lambda q1, n, st, sn, q2, c: _resolve_case(c),
    "RUN":   lambda q1, n, st, sn, q2, c: _resolve_run(c),
    "GRAPH": lambda q1, n, st, sn, q2, c: _resolve_graph(c),
    "TAX":   lambda q1, n, st, sn, q2, c: _resolve_tax(q1, q2, c),
    "INS":   lambda q1, n, st, sn, q2, c: _resolve_ins(q1, q2, c),
    "G":     lambda q1, n, st, sn, q2, c: _resolve_gap(n, c),
    "S":     lambda q1, n, st, sn, q2, c: _resolve_summary(n, st, sn, c),
    "CC":    lambda q1, n, st, sn, q2, c: _resolve_cross_cutting(n, st, sn, c),
    "I":     lambda q1, n, st, sn, q2, c: _resolve_indictment(n, st, sn, c),
    "E":     lambda q1, n, st, sn, q2, c: _resolve_entity(n, c),
    "A":     lambda q1, n, st, sn, q2, c: _resolve_audit(n, c),
}


def resolve_tag(family: str, qual1: str | None, num: str, subtype: str | None,
                subnum: str | None, qual2: str | None, context: dict) -> Any:
    """Look up tag in context structure. Returns None if unknown."""
    fn = _DISPATCH.get(family)
    if fn is None:
        return None
    return fn(qual1, num, subtype, subnum, qual2, context)
