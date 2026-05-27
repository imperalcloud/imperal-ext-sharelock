"""Sharelock-v2 — Pydantic return models for read handlers.

Federal V23 contract (SDK 5.0.1+): every @chat.function(action_type="read", ...)
MUST declare data_model=. Field names follow CHANGELOG 5.0.1 symmetry rule —
mirror input *Params field names where overlap exists to keep $REF paths
drift-free across chain steps.
"""
from __future__ import annotations

from pydantic import BaseModel


class CaseRecord(BaseModel):
    """One row from list_cases / case-summary lookups.

    Mirrors the dict shape produced by handlers.py:117-122 and the case fields
    rendered in handlers.py:367-369.
    """
    id: int
    name: str
    analysis_status: str | None = None
    status: str | None = None
    file_count: int = 0


class DocSearchHit(BaseModel):
    """One result row from search_docs.

    Cases API response shape varies (sometimes list-of-hits, sometimes
    {"results": []} for 404). Fields are permissive — only case_id is
    enforced via symmetry with SearchDocsParams.case_id.
    """
    doc_id: str | None = None
    snippet: str | None = None
    score: float | None = None
    case_id: int | None = None


class GapReviewItem(BaseModel):
    """One gap from review_analysis_gaps.gaps[] / by_severity[severity][].

    The envelope (case_id, run_id, by_severity, confidence_*) is the wrapper;
    GapReviewItem describes a single gap row per SDK 5.0.1 CHANGELOG pattern.
    """
    id: int | None = None
    severity: str | None = None
    description: str | None = None


class CaseChatResponse(BaseModel):
    """case_chat envelope — single `state` discriminator field.

    Used as data_model= on the conversational catch-all handler. Note:
    case_chat stays chain_callable=False (see Task 5) because it consumes
    ctx.history — typed dispatch drops history. The data_model declaration
    is for V23 compliance only; runtime path is the wrapper-LLM flow.
    """
    state: str
