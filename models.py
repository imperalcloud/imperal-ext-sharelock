"""Sharelock-v2 — SDL return models for chat handlers (100% SDL).

Federal V23/V24 (SDK 5.0.1+): every @chat.function data tool declares
``data_model=``. SDL doctrine (CLAUDE.md Rule 13): ТОЛЬКО SDL, ноль legacy —
single-entity / write-receipt returns are real ``sdl.Entity`` subclasses; LIST
returns are real ``sdl.EntityList[T]`` (``items=[...]``, ``x-sdl='entity-list'``),
NOT legacy ``{key:[dict]}`` BaseModel wrappers.

Federal I-EXT-RECORD-FIELD-NAMING-SYMMETRIC: every field name mirrors the ACTUAL
runtime dict key the handler hands to ``ActionResult.success(data=...)`` (verified
against handlers.py / handlers_analysis.py). Canonical ``id``/``title``/``kind``
are derived from those existing fields via a ``mode="before"`` validator so every
construction site (raw Cases API dicts) keeps working unchanged.

Per-row types (CaseRecord, DocSearchHit, GapReviewItem) are the ``sdl.EntityList``
item types — reused directly as the list-item entities, mirroring admin's pattern
(UserRecord nested in UserListResponse).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import model_validator

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


# ── List responses (real sdl.EntityList[T] — NO legacy {key:[dict]} wrappers) ───


class CaseListResponse(sdl.EntityList[CaseRecord]):
    """list_cases return shape — a REAL sdl.EntityList[CaseRecord]
    (``items=[...]``, ``x-sdl='entity-list'``). The handler keeps the platform
    scalar ``count`` as an additive typed field (EntityList is a pydantic
    BaseModel, so additive fields are allowed). NO legacy ``{cases:[dict]}``
    wrapper — handler now returns ``data={"items": cases, "count": n}``.
    """
    count: int = 0


class DocSearchResponse(sdl.EntityList[DocSearchHit]):
    """search_docs return shape — a REAL sdl.EntityList[DocSearchHit]
    (``items=[...]``, ``x-sdl='entity-list'``). NO legacy ``{results:[dict]}``
    wrapper — the handler normalises the Cases API response (raw list OR
    ``{results:[...]}``) into ``data={"items": [...], "total": n}``.
    """
    pass


# ── Single-entity / write-receipt responses (real sdl.Entity subclasses) ────────
#
# Federal V23/V24: each receipt is a canonical SDL entity whose field names mirror
# the handler's real ``data`` dict keys (I-EXT-RECORD-FIELD-NAMING-SYMMETRIC). The
# canonical id/title/kind are derived from those existing keys via _sdl_canon. Error
# paths return ActionResult.error(...) with no data, so the entity models the
# success shape only.


class CaseChatResponse(sdl.Entity):
    """case_chat result — a canonical SDL entity carrying the conversation
    ``state`` discriminator (handlers.py: data={"state": <status|case_list|
    intelligence|intake>}). kind='case_chat'; canonical id/title <- state.
    Keeps the existing ``state`` field verbatim.
    """
    kind: str = "case_chat"
    state: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data["id"] = data.get("state") or data.get("id") or "case_chat"
            data.setdefault("title", data.get("state") or "case_chat")
            data.setdefault("kind", "case_chat")
        return data


class CreateCaseResponse(sdl.Entity, sdl.Caseable):
    """create_case receipt (handlers.py: data={"case_id", "name"}) — a canonical
    forensic-case entity (kind='case'). Canonical id <- ``case_id``; title <-
    ``name``. ``case_id`` falls back to the string "?" when the API omits an id,
    hence the permissive type. Keeps both fields verbatim.
    """
    kind: str = "case"
    case_id: Optional[Any] = None
    name: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data["id"] = data.get("case_id") or data.get("id") or ""
            data.setdefault("title", data.get("name") or data.get("case_id") or "")
            data.setdefault("kind", "case")
        return data


class SyncCasesResponse(sdl.Entity):
    """sync_cases receipt (handlers.py: data={"created", "skipped",
    "total_folders"}) — a canonical SDL entity describing one sync run. ``created``
    and ``skipped`` are Nextcloud folder-name lists; ``total_folders`` is the scan
    size. kind='case_sync'; canonical id/title summarise the run. All fields kept
    verbatim.
    """
    kind: str = "case_sync"
    created: list[str] = []
    skipped: list[str] = []
    total_folders: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            _created = data.get("created") or []
            data["id"] = data.get("id") or "case_sync"
            data.setdefault(
                "title",
                f"Synced {len(_created)} case(s) from "
                f"{data.get('total_folders', 0)} folder(s)",
            )
            data.setdefault("kind", "case_sync")
        return data


# ── Analysis-domain models (re-exported so existing imports keep working) ───────
#
# Split into models_analysis.py to keep both modules under the 300-LOC ceiling
# (CLAUDE.md Rule 6). Importers (handlers_analysis.py) still do
# ``from models import GapReviewResponse, RunAnalysisResponse, ...`` unchanged.

from models_analysis import (  # noqa: E402, F401
    GapReviewItem,
    GapReviewResponse,
    RunAnalysisResponse,
    CancelAnalysisResponse,
    GapDecisionResponse,
)

# Share / upload / admin-settings domain — split into models_share.py (same
# 300-LOC-ceiling rationale); importers keep ``from models import ...``.

from models_share import (  # noqa: E402, F401
    CaseShareRecord,
    CaseShareListResponse,
    ShareCaseResponse,
    UnshareCaseResponse,
    UploadReceipt,
    SaveSettingsResponse,
)

__all__ = [
    "CaseRecord",
    "DocSearchHit",
    "GapReviewItem",
    "CaseListResponse",
    "DocSearchResponse",
    "GapReviewResponse",
    "CaseChatResponse",
    "CreateCaseResponse",
    "SyncCasesResponse",
    "RunAnalysisResponse",
    "CancelAnalysisResponse",
    "GapDecisionResponse",
    "CaseShareRecord",
    "CaseShareListResponse",
    "ShareCaseResponse",
    "UnshareCaseResponse",
    "UploadReceipt",
    "SaveSettingsResponse",
]
