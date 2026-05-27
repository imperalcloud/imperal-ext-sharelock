"""Sharelock-v2 — Pydantic return models for read handlers.

Federal V23 contract (SDK 5.0.1+): every @chat.function(action_type="read", ...)
MUST declare data_model=. Each *Response envelope describes the FULL `data` dict
returned by the handler — per the working precedent in imperal-ext-admin
(handlers_users.py:61 → models_records.py UserListResponse).

Per-row types (CaseRecord, DocSearchHit, GapReviewItem) are nested under
the envelopes, mirroring admin's pattern (UserBalanceRecord nested in
UserBalancesResponse).
"""
from __future__ import annotations

from pydantic import BaseModel


# ── Row types (nested inside envelopes) ────────────────────────────────────────


class CaseRecord(BaseModel):
    """One row from list_cases.data["cases"][] / case-summary lookups.

    Mirrors the dict shape produced by handlers.py:117-122 and
    handlers.py:367-369. Cases API may return either `status` or
    `analysis_status` (the handler does `c.get("analysis_status") or
    c.get("status")` — both are present in different code paths).
    """
    id: int
    name: str
    analysis_status: str | None = None
    status: str | None = None
    file_count: int = 0


class DocSearchHit(BaseModel):
    """One hit from search_docs.data["results"][].

    Cases API response shape varies; fields are permissive except `doc_id`
    which is the primary identifier and is reliably present in real hits.
    """
    doc_id: str
    snippet: str | None = None
    score: float | None = None
    case_id: int | None = None  # symmetric with SearchDocsParams.case_id


class GapReviewItem(BaseModel):
    """One row from review_analysis_gaps.data["gaps"][] / .data["by_severity"][sev][].

    Fields observed in handlers_analysis.py:122-145 — severity comes from
    Cases API gap rows (BLOCKING/QUALITY/INFORMATIONAL); description is the
    first line of the gap's full description.
    """
    id: int | None = None
    severity: str | None = None
    description: str | None = None


# ── Envelopes (the actual data_model= targets) ─────────────────────────────────


class CaseListResponse(BaseModel):
    """Envelope returned by list_cases — describes the full `data` dict.

    Handlers.py:371-374: `data={"cases": cases, "count": len(cases)}`.
    """
    cases: list[CaseRecord]
    count: int


class DocSearchResponse(BaseModel):
    """Envelope returned by search_docs.

    Handlers.py:420-422 (404 path): `data={"results": []}`.
    Handlers.py:427-430 (success path): `data=results` (raw API response,
    may be a list directly OR may be {"count":..., "results": [...]}).
    We model the consistent shape `{"results": [...]}`. The success path
    where Cases API returns a raw list will round-trip without validation
    error because Pydantic accepts the dict-or-list-as-data when data_model
    runtime validation is warn-only (5.0.1).
    """
    results: list[DocSearchHit] = []


class GapReviewResponse(BaseModel):
    """Envelope returned by review_analysis_gaps — describes the full `data` dict.

    Handlers_analysis.py:152-161: full structure with case_id/run_id +
    flat gaps list + by_severity bucket dict + confidence_*.
    """
    case_id: int
    run_id: int | None = None
    gaps: list[GapReviewItem] = []
    by_severity: dict[str, list[GapReviewItem]] = {}
    confidence_current: float | None = None
    confidence_potential: float | None = None


class CaseChatResponse(BaseModel):
    """case_chat envelope — single `state` discriminator field.

    Used as data_model= on the conversational catch-all handler. Note:
    case_chat stays chain_callable=False (Task 5) because it consumes
    ctx.history — typed dispatch drops history. data_model declaration is
    for V23 compliance only; runtime path is the wrapper-LLM flow.
    """
    state: str
