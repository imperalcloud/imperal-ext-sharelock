"""Sharelock-v2 — SDL return models for Track D (D2 report / D3 deletes / D4 drill-down).

Split out of models.py to keep each module under the 300-LOC ceiling
(CLAUDE.md Rule 6) — same pattern as models_analysis.py / models_share.py.
Re-exported from the canonical ``models`` module so handlers import from one place.

Federal V23/V24 + SDL doctrine (CLAUDE.md Rule 13): ТОЛЬКО SDL — list reads are
REAL ``sdl.EntityList[T]`` (``items=[...]`` + native ``total``/``has_more`` carry
the cap/total so big cases don't blow the cache/envelope); single reads + write
receipts are canonical ``sdl.Entity`` subclasses. Federal
I-EXT-RECORD-FIELD-NAMING-SYMMETRIC: every field name mirrors the ACTUAL runtime
dict key the handler hands to ``ActionResult.success(data=...)`` (verified against
handlers_control.py / handlers_drilldown.py).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import model_validator

from imperal_sdk import sdl


# ── D2: report link (single entity) ─────────────────────────────────────────────


class ReportLink(sdl.Entity):
    """get_report result — a signed, short-lived report download link OR a typed
    "not available yet" fact. On a completed run: data={"available": True, "url",
    "format", "expires_in_seconds", "run_id"}. With no completed run:
    data={"available": False, "reason"} (a SUCCESS fact, never an error).
    kind='report_link'. The ``url`` rides the canonical ``sdl.Entity.url`` slot.
    """
    kind: str = "report_link"
    available: bool = False
    format: Optional[str] = None
    expires_in_seconds: Optional[int] = None
    run_id: int | str | None = None
    reason: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", str(data.get("id") or data.get("run_id") or "report"))
            data.setdefault("title", data.get("title") or "Forensic report")
            data.setdefault("kind", "report_link")
        return data


# ── D3: delete receipts (single entities) ───────────────────────────────────────


class CaseDeleteReceipt(sdl.Entity):
    """delete_case receipt (handlers_control.py: data={"deleted", "case_id",
    "note"}). kind='delete_receipt'; canonical id <- case_id, title <- case name.
    """
    kind: str = "delete_receipt"
    deleted: bool = False
    case_id: Optional[int] = None
    note: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", str(data.get("case_id") or "case"))
            data.setdefault("title", data.get("title")
                            or f"Case {data.get('case_id', '?')} deleted")
            data.setdefault("kind", "delete_receipt")
        return data


class FileDeleteReceipt(sdl.Entity):
    """delete_file receipt (handlers_control.py: data={"deleted", "case_id",
    "file_id"}). kind='delete_receipt'; canonical id <- file_id.
    """
    kind: str = "delete_receipt"
    deleted: bool = False
    case_id: Optional[int] = None
    file_id: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", str(data.get("file_id") or "file"))
            data.setdefault(
                "title",
                data.get("title") or f"File {data.get('file_id', '?')} deleted")
            data.setdefault("kind", "delete_receipt")
        return data


class UpdateCaseReceipt(sdl.Entity, sdl.Caseable):
    """update_case receipt (handlers_control.py: data={"updated", "case_id",
    "name", "description"}). kind='case'; canonical id <- case_id, title <- name.
    """
    kind: str = "case"
    updated: bool = False
    case_id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", str(data.get("case_id") or "case"))
            data.setdefault("title", data.get("name")
                            or f"Case {data.get('case_id', '?')}")
            data.setdefault("kind", "case")
        return data


# ── D4: single-entity reads ─────────────────────────────────────────────────────


class CaseDetail(sdl.Entity, sdl.Caseable):
    """get_case_detail result — one case row (status / analysis_status /
    file_count / active_run_id / created_at). kind='case'.
    """
    kind: str = "case"
    name: Optional[str] = None
    status: Optional[str] = None
    analysis_status: Optional[str] = None
    file_count: Optional[int] = None
    active_run_id: int | str | None = None
    created_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("id") or 0)
            data.setdefault("title", data.get("name") or data.get("id") or "")
            data.setdefault("kind", "case")
        return data


class AnalysisStatus(sdl.Entity):
    """analysis_status result — the case's analysis state snapshot. data={"status",
    "analysis_status", "active_run_id", "version", "case_id"}. kind='analysis_status'.
    """
    kind: str = "analysis_status"
    case_id: Optional[int] = None
    status: Optional[str] = None
    analysis_status: Optional[str] = None
    active_run_id: int | str | None = None
    version: int | str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", str(data.get("case_id") or "analysis"))
            data.setdefault(
                "title",
                f"Analysis {data.get('analysis_status') or data.get('status') or '?'}")
            data.setdefault("kind", "analysis_status")
        return data


class IntelligenceGraphSummary(sdl.Entity):
    """get_intelligence_graph result — a SUMMARY (never the raw multi-thousand
    node dump): data={"node_count", "edge_count", "type_count",
    "type_breakdown": [{type,count}], "top_entities": [...], "note"}. The
    type_breakdown mirrors the Graph tab's clustered overview; the capped
    top-entity list rides ``top_entities`` (NOT items — this is a single summary
    entity, not an EntityList). kind='intelligence_graph'.
    """
    kind: str = "intelligence_graph"
    case_id: Optional[int] = None
    node_count: int = 0
    edge_count: int = 0
    type_count: int = 0
    type_breakdown: list[dict[str, Any]] = []
    top_entities: list[dict[str, Any]] = []
    note: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", str(data.get("case_id") or "graph"))
            data.setdefault(
                "title",
                f"Intelligence graph: {data.get('node_count', 0)} entities, "
                f"{data.get('edge_count', 0)} relationships")
            data.setdefault("kind", "intelligence_graph")
        return data


class EntityRecord(sdl.Entity):
    """One intelligence-graph entity row (list_entities / get_entity). Cases API
    rows carry type / value|normalized_value / mention_count (+ id|entity_id).
    kind='intel_entity'.
    """
    kind: str = "intel_entity"
    type: Optional[str] = None
    value: Optional[str] = None
    normalized_value: Optional[str] = None
    mention_count: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("id") or data.get("entity_id") or "")
            data.setdefault("title", data.get("value")
                            or data.get("normalized_value")
                            or str(data.get("id") or data.get("entity_id") or ""))
            data.setdefault("kind", "intel_entity")
        return data


__all__ = [
    "ReportLink",
    "CaseDeleteReceipt",
    "FileDeleteReceipt",
    "UpdateCaseReceipt",
    "CaseDetail",
    "AnalysisStatus",
    "IntelligenceGraphSummary",
    "EntityRecord",
]
