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

from pydantic import BaseModel, model_validator

from imperal_sdk import sdl


# ── Row types (nested inside envelopes) ────────────────────────────────────────
#
# SDL migration (SDK 5.2.0): per-row types are now ``sdl.Entity`` subclasses
# composed with the facets that fit their fields. This is a STRICTLY ADDITIVE
# change — every existing field is kept verbatim (panels, the gap-review UI, and
# the kernel data_model validators rely on them). The canonical ``id``/``title``/
# ``kind`` are derived from existing fields via a mode="before" validator so all
# existing construction sites (raw Cases API dicts) keep working unchanged.


class CaseRecord(sdl.Entity, sdl.Caseable):
    """One row from list_cases.data["cases"][] / case-summary lookups.

    Mirrors the dict shape produced by handlers.py:117-122 and
    handlers.py:367-369. Cases API may return either `status` or
    `analysis_status` (the handler does `c.get("analysis_status") or
    c.get("status")` — both are present in different code paths).

    SDL: a forensic investigation case → ``sdl.Caseable`` (sec.case_*).
    Canonical id <- existing ``id``; title <- existing ``name``.
    """
    kind: str = "case"
    # --- existing fields kept verbatim (panels / gap-review / API rows rely on them) ---
    name: str
    analysis_status: str | None = None
    status: str | None = None
    file_count: int = 0

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("id") or 0)
            data.setdefault("title", data.get("name") or data.get("id") or "")
        return data


class DocSearchHit(sdl.Entity):
    """One hit from search_docs.data["results"][].

    Cases API response shape varies; fields are permissive except `doc_id`
    which is the primary identifier and is reliably present in real hits.

    SDL: a single document search hit. Canonical id <- existing ``doc_id``;
    title <- ``snippet`` (falling back to ``doc_id``). No standard facet maps
    cleanly to ``score`` (float relevance) / ``snippet`` without a type clash,
    so this stays a bare ``sdl.Entity``.
    """
    kind: str = "doc_hit"
    # --- existing fields kept verbatim ---
    doc_id: str
    snippet: str | None = None
    score: float | None = None
    case_id: int | None = None  # symmetric with SearchDocsParams.case_id

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("doc_id") or "")
            data.setdefault("title", data.get("snippet") or data.get("doc_id") or "")
        return data


class GapReviewItem(sdl.Entity):
    """One row from review_analysis_gaps.data["gaps"][] / .data["by_severity"][sev][].

    Fields observed in handlers_analysis.py:122-145 — severity comes from
    Cases API gap rows (BLOCKING/QUALITY/INFORMATIONAL); description is the
    first line of the gap's full description.

    SDL: a single analysis gap. Canonical id <- existing ``id``; title <-
    ``description``. NOTE deliberately NOT mixing ``sdl.Prioritized``: its
    ``severity`` is a fixed Literal['info','minor','major','critical'] that
    would reject Sharelock's BLOCKING/QUALITY/INFORMATIONAL values — keeping
    the existing free-string ``severity`` preserves back-compat.
    """
    kind: str = "gap"
    # --- existing fields kept verbatim ---
    severity: str | None = None
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("id") or "")
            data.setdefault("title", data.get("description") or "")
        return data


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
