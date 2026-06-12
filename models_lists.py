"""Sharelock-v2 — SDL list-row types + list responses for Track D4 drill-down.

Split out of models_drilldown.py to keep each module under the 300-LOC ceiling
(CLAUDE.md Rule 6). Re-exported from the canonical ``models`` module so handlers
import from one place.

Federal V23 + SDL doctrine (Rule 13): every list read is a REAL
``sdl.EntityList[T]`` (``items=[...]`` + native ``total``/``has_more`` carry the
cap/total so a 2655-row case never blows the cache/envelope). Each row type is a
canonical ``sdl.Entity`` (+ a fitting facet); field names mirror the ACTUAL Cases
API row keys (I-EXT-RECORD-FIELD-NAMING-SYMMETRIC).

``EntityRecord`` lives in models_drilldown.py (it is also the single get_entity
return type) and is imported here as the list-item type for list_entities.
"""
from __future__ import annotations

from typing import Optional

from pydantic import model_validator

from imperal_sdk import sdl

from models_drilldown import EntityRecord


# ── List-row types (sdl.EntityList items) ───────────────────────────────────────


class CaseFileRecord(sdl.Entity, sdl.FileObject):
    """One evidence-file row (list_case_files). Cases API file rows carry
    filename / size / mime_type (+ id|file_id). kind='case_file'. ``size`` is the
    handler's verbatim key; ``sdl.FileObject`` adds the canonical filename/
    mime_type/size_bytes facet roles.
    """
    kind: str = "case_file"
    file_id: int | str | None = None
    size: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("id") or data.get("file_id") or "")
            data.setdefault("title", data.get("filename")
                            or data.get("name") or str(data.get("id") or ""))
            data.setdefault("kind", "case_file")
        return data


class RelationshipRecord(sdl.Entity):
    """One entity-relationship row (list_relationships). Cases API rows carry
    source / target / rel_type|type (+ id|relationship_id). kind='relationship'.
    """
    kind: str = "relationship"
    source: Optional[str] = None
    target: Optional[str] = None
    rel_type: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("id") or data.get("relationship_id") or "")
            data.setdefault(
                "title",
                f"{data.get('source', '?')} → {data.get('target', '?')}")
            data.setdefault("kind", "relationship")
        return data


class TimelineEventRecord(sdl.Entity, sdl.Timestamped):
    """One timeline-event row (list_timeline_events). kind='timeline_event'."""
    kind: str = "timeline_event"
    event_type: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("id") or data.get("event_id") or "")
            data.setdefault("title", data.get("description")
                            or data.get("event_type") or str(data.get("id") or ""))
            data.setdefault("kind", "timeline_event")
        return data


class TaxonomyRecord(sdl.Entity):
    """One OSAC taxonomy row (get_taxonomy): category / subcategory / file_count /
    total_size. kind='taxonomy_category'.
    """
    kind: str = "taxonomy_category"
    category: Optional[str] = None
    subcategory: Optional[str] = None
    file_count: Optional[int] = None
    total_size: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            cat = data.get("category") or ""
            sub = data.get("subcategory") or ""
            data.setdefault("id", f"{cat}/{sub}" if sub else (cat or "category"))
            data.setdefault("title", f"{cat} / {sub}" if sub else (cat or "?"))
            data.setdefault("kind", "taxonomy_category")
        return data


class AuditEventRecord(sdl.Entity, sdl.Auditable):
    """One audit/chain-of-custody event row (get_audit_log). Cases API rows carry
    event_type / actor / occurred_at. kind='audit_event'; ``sdl.Auditable`` adds
    the canonical actor/action/occurred_at facet roles.
    """
    kind: str = "audit_event"
    event_type: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("id") or data.get("audit_id") or "")
            data.setdefault("title", data.get("event_type")
                            or data.get("action") or "audit event")
            data.setdefault("kind", "audit_event")
            # mirror the Cases API key into the canonical Auditable role
            data.setdefault("action", data.get("event_type"))
        return data


class AnalysisRunRecord(sdl.Entity, sdl.Versioned):
    """One analysis-run row (list_analysis_runs): run_id / version / status.
    kind='analysis_run'; ``sdl.Versioned`` adds the canonical version role.
    """
    kind: str = "analysis_run"
    run_id: int | str | None = None
    status: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("run_id") or data.get("id") or "")
            data.setdefault("title", f"v{data.get('version', '?')}")
            data.setdefault("kind", "analysis_run")
        return data


# ── List responses (REAL sdl.EntityList[T] — native total/has_more) ─────────────


class CaseFileListResponse(sdl.EntityList[CaseFileRecord]):
    """list_case_files — sdl.EntityList[CaseFileRecord] (items + total/has_more)."""
    case_id: Optional[int] = None


class EntityListResponse(sdl.EntityList[EntityRecord]):
    """list_entities — sdl.EntityList[EntityRecord] (items + total/has_more)."""
    case_id: Optional[int] = None


class RelationshipListResponse(sdl.EntityList[RelationshipRecord]):
    """list_relationships — sdl.EntityList[RelationshipRecord]."""
    case_id: Optional[int] = None


class TimelineEventListResponse(sdl.EntityList[TimelineEventRecord]):
    """list_timeline_events — sdl.EntityList[TimelineEventRecord]."""
    case_id: Optional[int] = None


class TaxonomyListResponse(sdl.EntityList[TaxonomyRecord]):
    """get_taxonomy — sdl.EntityList[TaxonomyRecord]."""
    case_id: Optional[int] = None


class AuditLogResponse(sdl.EntityList[AuditEventRecord]):
    """get_audit_log — sdl.EntityList[AuditEventRecord] (chain-of-custody view).
    Additive ``verified``: the hash-chain integrity verdict when available.
    """
    case_id: Optional[int] = None
    verified: Optional[bool] = None


class AnalysisRunListResponse(sdl.EntityList[AnalysisRunRecord]):
    """list_analysis_runs — sdl.EntityList[AnalysisRunRecord]."""
    case_id: Optional[int] = None


__all__ = [
    "CaseFileRecord",
    "RelationshipRecord",
    "TimelineEventRecord",
    "TaxonomyRecord",
    "AuditEventRecord",
    "AnalysisRunRecord",
    "CaseFileListResponse",
    "EntityListResponse",
    "RelationshipListResponse",
    "TimelineEventListResponse",
    "TaxonomyListResponse",
    "AuditLogResponse",
    "AnalysisRunListResponse",
]
