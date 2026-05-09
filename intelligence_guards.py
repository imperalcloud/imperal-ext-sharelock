"""
Sharelock v2 — Intelligence chat guards (Layer 3 only).

Layer 1 (citation grounding) and Layer 2 (history filtering for factual
queries) were superseded by the structured tool_use response in chat.py:

  - Citations live in IntelligenceResponse.claims (structured) and are
    validated by intelligence_validator.validate_grounded_claims —
    no regex tag scanning of LLM prose.
  - History filtering went away with substring-based is_factual_query;
    factual chat now runs on grounded CASE CONTEXT alone (empty history).
    case_resolver already pins case_id from message anaphora.

What's left here:
  - context_fingerprint(ctx_data) — Layer 3 deterministic hash of run
    state, used by chat.run_intelligence to invalidate ctx.cache when
    the analysis run advances.
"""
from __future__ import annotations

import hashlib


def context_fingerprint(ctx_data: dict) -> str:
    """Layer 3: short deterministic hash of stateful fields that identify
    a particular analysis run.

    Two different runs (or completed vs in-progress on the same run)
    produce different fingerprints. When the fingerprint changes, the
    cache key for this case is rotated so stale state can't bleed in.
    """
    case = ctx_data.get("case") or {}
    run = ctx_data.get("run") or {}
    payload = (
        f"{case.get('active_run_id')}|"
        f"{case.get('analysis_version')}|"
        f"{case.get('analysis_status')}|"
        f"{run.get('status')}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


__all__ = ["context_fingerprint"]
