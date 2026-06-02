"""Sharelock-v2 — SDL return models for the ANALYSIS domain (100% SDL).

Split out of models.py to keep each module under the 300-LOC ceiling
(CLAUDE.md Rule 6). These are re-exported from the canonical ``models`` module
so existing ``from models import ...`` sites keep working unchanged.

Federal V23/V24 + SDL doctrine (CLAUDE.md Rule 13): ТОЛЬКО SDL, ноль legacy —
the gap-review read returns a real ``sdl.EntityList[GapReviewItem]``; the
run-lifecycle / gap-decision writes return canonical ``sdl.Entity`` receipts.
Federal I-EXT-RECORD-FIELD-NAMING-SYMMETRIC: every field name mirrors the ACTUAL
runtime dict key the handler hands to ``ActionResult.success(data=...)`` (verified
against handlers_analysis.py).
"""
from __future__ import annotations

from typing import Optional

from pydantic import model_validator

from imperal_sdk import sdl


# ── Row type (sdl.EntityList item) ─────────────────────────────────────────────


class GapReviewItem(sdl.Entity):
    """One row from review_analysis_gaps — the flat gaps list and the
    severity-bucketed ``by_severity`` map both carry these items.

    Fields observed in handlers_analysis.py — severity comes from Cases API gap
    rows (BLOCKING/QUALITY/INFORMATIONAL); description is the first line of the
    gap's full description.

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


# ── List response (real sdl.EntityList[T] — NO legacy {key:[dict]} wrapper) ─────


class GapReviewResponse(sdl.EntityList[GapReviewItem]):
    """review_analysis_gaps return shape — a REAL sdl.EntityList[GapReviewItem]
    (the flat gap list flows through ``items``). The handler keeps the platform
    scalars ``case_id``/``run_id``/``confidence_*`` and the severity-bucketed
    ``by_severity`` map as additive typed fields below (EntityList is a pydantic
    BaseModel). NO legacy ``{gaps:[dict]}`` wrapper — handler now returns
    ``data={"items": gaps, "case_id": ..., "by_severity": {...}, ...}``.
    """
    case_id: int = 0
    run_id: int | str | None = None
    by_severity: dict[str, list[GapReviewItem]] = {}
    confidence_current: float | None = None
    confidence_potential: float | None = None


# ── Write-receipt responses (real sdl.Entity subclasses) ────────────────────────
#
# Federal V24: each receipt is a canonical SDL entity whose field names mirror the
# handler's real ``data`` dict keys (I-EXT-RECORD-FIELD-NAMING-SYMMETRIC); the
# canonical id/title/kind are derived from those keys via _sdl_canon. Error paths
# return ActionResult.error(...) with no data, so the entity models success only.


class RunAnalysisResponse(sdl.Entity):
    """run_analysis receipt (handlers_analysis.py: data={"case_id", "status",
    "run_id", "version"}) — a canonical SDL entity for one analysis run.
    Canonical id <- ``run_id`` (falling back to ``case_id``); title <- run label.
    ``run_id``/``version`` come straight from the Cases API start response and may
    be absent on a partial response. All fields kept verbatim.
    """
    kind: str = "analysis_run"
    case_id: Optional[int] = None
    status: Optional[str] = None
    run_id: int | str | None = None
    version: int | str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data["id"] = (
                data.get("run_id") or data.get("id") or data.get("case_id") or ""
            )
            data.setdefault(
                "title",
                f"Analysis run #{data.get('run_id', '?')} "
                f"(case {data.get('case_id', '?')})",
            )
            data.setdefault("kind", "analysis_run")
        return data


class CancelAnalysisResponse(sdl.Entity):
    """cancel_analysis receipt (handlers_analysis.py: data={"case_id", "run_id",
    "status"}) — a canonical SDL entity. Canonical id <- ``run_id`` (falling back
    to ``case_id``); title <- run label. ``run_id`` falls back to the string "?"
    when the API omits it. All fields kept verbatim.
    """
    kind: str = "analysis_run"
    case_id: Optional[int] = None
    run_id: int | str | None = None
    status: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data["id"] = (
                data.get("run_id") or data.get("id") or data.get("case_id") or ""
            )
            data.setdefault(
                "title",
                f"Analysis run #{data.get('run_id', '?')} "
                f"(case {data.get('case_id', '?')})",
            )
            data.setdefault("kind", "analysis_run")
        return data


class GapDecisionResponse(sdl.Entity):
    """Shared receipt for the two gap-decision write handlers — continue_analysis
    (decision="continue") and resume_with_new_evidence (decision="add_evidence")
    both return data={"case_id", "run_id", "decision"}. A canonical SDL entity;
    canonical id <- ``run_id`` (falling back to ``case_id``); title <- decision
    label. All fields kept verbatim.
    """
    kind: str = "analysis_run"
    case_id: Optional[int] = None
    run_id: int | str | None = None
    decision: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data["id"] = (
                data.get("run_id") or data.get("id") or data.get("case_id") or ""
            )
            data.setdefault(
                "title",
                f"Gap decision '{data.get('decision', '?')}' "
                f"(case {data.get('case_id', '?')}, run #{data.get('run_id', '?')})",
            )
            data.setdefault("kind", "analysis_run")
        return data
