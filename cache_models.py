"""Sharelock v2 · ctx.cache model registrations (SDK v1.6.0).

Pydantic models backed by ``ctx.cache`` for short-lived per-user caching.
Each model is registered with ``@ext.cache_model(<name>)`` — set/get calls
in handlers/panels resolve the class via this registry.

Constraints (enforced by CacheClient):
- value serialised <= 64 KB (I-CACHE-VALUE-SIZE-CAP-64KB)
- TTL in [5, 300] s (I-CACHE-TTL-CAP-300S)
- key syntax ``[A-Za-z0-9_\\-:]+`` length <= 128 (I-CACHE-KEY-SAFETY)

Per I-SKELETON-LLM-ONLY + I-MIGRATION-SHARELOCK-SKELETON-SINGLE-OWNER:
- ``case_summary`` replaces the legacy ``ctx.skeleton_data["case_status"]``
  read-path for panels/handlers. The skeleton (``skeleton_refresh_case_status``)
  remains the writer for the classifier envelope scalar summary; the cache
  carries the full per-render dict used by panels + chat.
- ``case_context_fingerprint`` replaces the ``ctx.skeleton_data
  ["_chat_context_fingerprint"]`` write that the INTELLIGENCE state uses to
  detect when V3 pipeline state advanced between turns (assistant history
  drop on change).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app import ext


@ext.cache_model("case_summary")
class CaseSummary(BaseModel):
    """Per-render cache of the user's cases + active-case snapshot.

    Shape mirrors ``case_resolver.load_case_data_from_api`` return value so
    downstream consumers (``chat_engine.case_list_response``, INTAKE/
    INTELLIGENCE prompt builders, Report tab) can treat the dumped dict
    identically to the old skeleton payload.

    Fields are all optional because the API may return partial data when
    a case has no analysis run yet or Cases API is degraded.
    """
    cases: list[dict[str, Any]] = Field(default_factory=list)
    active_case_id: int | None = None
    case_name: str = ""
    analysis_status: str | None = None
    file_count: int = 0
    files: list[dict[str, Any]] = Field(default_factory=list)
    # Optional extras surfaced by panels_case when the skeleton already had them
    analysis_progress: dict[str, Any] | None = None
    analysis_version: str = "1.0"
    outdated: bool = False
    key_entities: list[dict[str, Any]] = Field(default_factory=list)


@ext.cache_model("case_context_fingerprint")
class CaseContextFingerprint(BaseModel):
    """Content fingerprint of the V3 grounded-context blob for a case.

    Used by chat.run_intelligence to detect when V3 analysis state advanced
    (new run_id / version / status) between conversation turns — on change
    the assistant history is dropped to prevent stale-grounded answers.
    """
    case_id: int
    fingerprint: str



@ext.cache_model("nc_folder_listing")
class NextcloudFolderListing(BaseModel):
    """Cached top-level Nextcloud folder list (panel left sidebar).

    Stale-fallback when NC PROPFIND is slow or Cases API is overloaded.
    TTL ~60s — folder set rarely changes, panel UX prefers stale render
    over Temporal-fallback on every click.
    """
    folders: list[str] = Field(default_factory=list)


@ext.cache_model("nc_file_listing")
class NextcloudFileListing(BaseModel):
    """Cached recursive file listing for a single case folder.

    Cap at 200 entries to stay under the 64KB cache value cap (each entry
    is ~150-300 bytes). The panel slices to [:100] for render anyway.
    TTL ~30s — files change during ingestion, but stale render beats hang.
    """
    files: list[dict[str, Any]] = Field(default_factory=list)


@ext.cache_model("user_cases_listing")
class UserCasesListing(BaseModel):
    """Cached `queries.get_cases(user_id)` result for panel rendering.

    Captures the full list returned by Cases API GET /cases?user_id=. Cap
    by truncation if user has > 100 cases (federal customers typically
    have < 50). TTL ~30s — case status flips during analysis but panel
    falls back to cached snapshot when Cases API is slow.
    """
    cases: list[dict[str, Any]] = Field(default_factory=list)
