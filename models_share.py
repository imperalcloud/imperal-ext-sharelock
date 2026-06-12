"""Sharelock-v2 — SDL return models for the SHARE / UPLOAD / ADMIN domain.

Split out of models.py to keep each module under the 300-LOC ceiling
(CLAUDE.md Rule 6) — same pattern as models_analysis.py. Re-exported from the
canonical ``models`` module so handlers import from one place.

Federal V23/V24 + SDL doctrine (CLAUDE.md Rule 13): ТОЛЬКО SDL — the share
listing is a real ``sdl.EntityList[CaseShareRecord]``; the write receipts are
canonical ``sdl.Entity`` subclasses. Federal
I-EXT-RECORD-FIELD-NAMING-SYMMETRIC: every field name mirrors the ACTUAL
runtime dict key the handler hands to ``ActionResult.success(data=...)``
(verified against handlers_share.py / handlers_files.py / handlers_admin.py).
SaveSettingsResponse is MASKED by design — it never models a secret field.
"""
from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from imperal_sdk import sdl


# ── Row type (sdl.EntityList item) ─────────────────────────────────────────────


class CaseShareRecord(sdl.Entity):
    """One grant row from list_case_shares.data["items"][] — mirrors the Cases
    API GET /cases/{id}/shares ``shares`` rows (imperal_id, granted_by,
    created_at + LEFT-JOINed users.email/name, both nullable for grants whose
    imperal_id has no users row yet).

    SDL: canonical id <- ``imperal_id``; title <- email|name|imperal_id;
    kind='case_share'.
    """
    kind: str = "case_share"
    # --- existing fields kept verbatim (raw Cases API rows ride through) ---
    imperal_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    granted_by: Optional[str] = None
    created_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("imperal_id") or "")
            data.setdefault("title", data.get("email") or data.get("name")
                            or data.get("imperal_id") or "")
        return data


# ── List response (real sdl.EntityList[T]) ──────────────────────────────────────


class CaseShareListResponse(sdl.EntityList[CaseShareRecord]):
    """list_case_shares return shape — a REAL sdl.EntityList[CaseShareRecord]
    (``items=[...]``). Additive scalars: ``case_id`` + ``owner`` (the case
    creator row ``{imperal_id, email, name}`` or None).
    """
    case_id: Optional[int] = None
    owner: Optional[dict] = None


# ── Write receipts (canonical sdl.Entity subclasses) ────────────────────────────


class ShareCaseResponse(sdl.Entity):
    """share_case receipt (handlers_share.py: data={"shared", "case_id",
    "imperal_id", "note"}). ``note`` is the honesty fact — set when the
    identifier doesn't look like an imperal id (stored verbatim; server-side
    email resolution lands later). kind='case_share'.
    """
    kind: str = "case_share"
    shared: bool = False
    case_id: Optional[int] = None
    imperal_id: Optional[str] = None
    note: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("imperal_id") or "case_share")
            data.setdefault("title", data.get("imperal_id") or "case share")
        return data


class UnshareCaseResponse(sdl.Entity):
    """unshare_case receipt (handlers_share.py: data={"unshared", "deleted",
    "case_id", "imperal_id"}). ``deleted`` mirrors the Cases API DELETE
    rowcount — 0 means there was no grant to revoke. kind='case_share'.
    """
    kind: str = "case_share"
    unshared: bool = False
    deleted: int = 0
    case_id: Optional[int] = None
    imperal_id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", data.get("imperal_id") or "case_share")
            data.setdefault("title", data.get("imperal_id") or "case share")
        return data


class UploadReceipt(sdl.Entity):
    """upload_case_files receipt (handlers_files.py: data={"uploaded",
    "case_id", "case_name", "files", "failed", "note"} on success and
    data={"uploaded": 0, "case_id", "files": [], "reason"} on a limit fact —
    limit violations are SUCCESS facts, not errors). kind='upload_receipt'.
    """
    kind: str = "upload_receipt"
    uploaded: int = 0
    case_id: Optional[int] = None
    case_name: Optional[str] = None
    files: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    reason: Optional[str] = None
    note: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", f"upload-case-{data.get('case_id') or '?'}")
            data.setdefault(
                "title",
                f"Uploaded {data.get('uploaded', 0)} file(s) to "
                f"{data.get('case_name') or data.get('case_id') or 'case'}",
            )
        return data


class SaveSettingsResponse(sdl.Entity):
    """save_agency_settings receipt (handlers_admin.py) — MASKED BY DESIGN:
    no secret field exists on this model; passwords surface ONLY as the
    ``storage_password_set`` boolean. data={"saved", "agency_id",
    "storage_url", "storage_username", "storage_base_path",
    "storage_password_set", "database_configured"} on success and
    data={"saved": False, "agency_id", "reason"} on the typed denial fact.
    kind='agency_settings'.
    """
    kind: str = "agency_settings"
    saved: bool = False
    agency_id: Optional[str] = None
    storage_url: Optional[str] = None
    storage_username: Optional[str] = None
    storage_base_path: Optional[str] = None
    storage_password_set: Optional[bool] = None
    database_configured: Optional[bool] = None
    reason: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", f"agency-settings-{data.get('agency_id') or '?'}")
            data.setdefault(
                "title",
                f"Agency settings for {data.get('agency_id') or '?'} "
                f"({'saved' if data.get('saved') else 'not saved'})",
            )
        return data


__all__ = [
    "CaseShareRecord",
    "CaseShareListResponse",
    "ShareCaseResponse",
    "UnshareCaseResponse",
    "UploadReceipt",
    "SaveSettingsResponse",
]
