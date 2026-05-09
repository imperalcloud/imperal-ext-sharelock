"""
Sharelock v2 — citation validator (structured input, no regex).

Consumes the structured `claims` array emitted by the LLM via tool_use
(see intelligence_response.py). Each claim carries decomposed source IDs
already; this module verifies them by calling the existing
intelligence_tag_resolver.resolve_tag dispatcher.

Two checks per source:
  1. unknown_id    — resolve_tag returns None (the source ID does not
                     exist in CASE CONTEXT).
  2. content_mismatch — for factual families (G/S/CC/I/E) the resolved
                     content tokens overlap < 25% with the claim text.
                     Cheap heuristic against fabricated alignment.

Tokenizer is char-by-char alphanumeric extraction — no regex. Word
tokenization is lexical, not pattern-matching for intent/behavior.

Returns a list of CitationIssue records. Caller decides whether to
log, append a warning footer, or surface to the user.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from intelligence_tag_resolver import resolve_tag


log = logging.getLogger("sharelock-v2.intelligence_validator")

# Citation families considered "factual" — content overlap check applies.
_FACTUAL_FAMILIES = {"G", "S", "CC", "I", "E"}

_STOPWORDS = {
    # English
    "the", "and", "but", "for", "from", "this", "that", "with", "per", "not",
    "are", "was", "were", "has", "have", "had", "will", "would", "been", "being",
    "one", "two", "all", "any", "some", "also", "than", "then", "there", "these",
    "those", "into", "your", "you", "our", "them", "their", "which", "what", "when",
    "where", "why", "how", "who", "about", "only", "just",
    # Russian
    "что", "это", "как", "для", "или", "без", "тоже", "всех", "всего", "этом",
    "были", "может", "если", "чтобы", "надо", "тот", "том", "ними", "него",
    "который", "которая", "которые", "этот", "эта", "эти", "всё", "весь", "вся",
    "один", "два", "при", "над", "под", "про", "нет", "уже",
}


@dataclass(frozen=True, slots=True)
class CitationIssue:
    """One validation finding against a single GroundedClaim source."""
    claim_text: str
    source_repr: str          # human-readable "I2:T1" form, for logs
    reason: str               # "unknown_id" | "content_mismatch"


def _format_source(source: Any) -> str:
    """Render a CitationSource as a debug-friendly string (for logs)."""
    family = getattr(source, "family", "?")
    qual1 = getattr(source, "qual1", None)
    num = getattr(source, "num", None)
    subtype = getattr(source, "subtype", None)
    subnum = getattr(source, "subnum", None)
    qual2 = getattr(source, "qual2", None)
    parts = [family]
    if qual1:
        parts[0] = f"{family}:{qual1}"
    if num:
        parts[0] = f"{family}{num}" if not qual1 else f"{family}:{qual1}:{num}"
    if subtype and subnum:
        parts[0] = f"{parts[0]}:{subtype}{subnum}"
    if qual2:
        parts[0] = f"{parts[0]}:{qual2}"
    return parts[0]


def _tokenize(text: str) -> list[str]:
    """Split text into 3+ char alphanumeric tokens (manual, no regex).

    Iterates char-by-char, accumulating alphanumeric / hyphen runs. Works
    for Latin + Cyrillic + digits. Output lowercased.
    """
    if not text:
        return []
    out: list[str] = []
    word: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch == "-":
            word.append(ch)
        elif word:
            if len(word) >= 3:
                out.append("".join(word))
            word = []
    if word and len(word) >= 3:
        out.append("".join(word))
    # Filter stopwords + pure-digit runs.
    return [t for t in out if t not in _STOPWORDS and not t.isdigit()]


def _flatten_context_text(content: Any) -> str:
    """Flatten nested dict/list to a single string for token matching."""
    try:
        if isinstance(content, str):
            return content
        if isinstance(content, (int, float)):
            return str(content)
        return json.dumps(content, ensure_ascii=False, default=str)
    except Exception:
        return str(content)


def semantic_alignment(claim_text: str, context_content: Any) -> bool:
    """Cheap heuristic: do >25% of claim tokens appear in context content?

    True  = aligned (or claim too short / no tokens to verify).
    False = potential content mismatch — flag for audit footer.
    """
    if context_content is None:
        return False
    claim_tokens = set(_tokenize(claim_text))
    if len(claim_tokens) < 3:
        return True
    context_tokens = set(_tokenize(_flatten_context_text(context_content)))
    if not context_tokens:
        return True
    overlap = claim_tokens & context_tokens
    denominator = min(len(claim_tokens), 10)
    return (len(overlap) / denominator if denominator else 0.0) > 0.25


def validate_grounded_claims(claims: Any, context: dict) -> list[CitationIssue]:
    """Validate every (claim, source) pair against CASE CONTEXT.

    `claims` is an iterable of GroundedClaim (or duck-typed equivalents).
    Returns a list of CitationIssue records — one per offending source.
    Empty list = clean.

    Pure structural validation. No regex, no string scanning of LLM prose.
    """
    issues: list[CitationIssue] = []
    for claim in claims or ():
        text = getattr(claim, "text", "") or ""
        sources = getattr(claim, "sources", ()) or ()
        for source in sources:
            family = getattr(source, "family", None)
            if not family:
                continue
            qual1 = getattr(source, "qual1", None)
            num = getattr(source, "num", None) or ""
            subtype = getattr(source, "subtype", None)
            subnum = getattr(source, "subnum", None)
            qual2 = getattr(source, "qual2", None)
            try:
                resolved = resolve_tag(family, qual1, num, subtype, subnum, qual2, context)
            except Exception as e:
                log.warning(f"resolve_tag raised on {family} (continuing): {e}")
                resolved = None
            source_repr = _format_source(source)
            if resolved is None:
                issues.append(CitationIssue(
                    claim_text=text[:200],
                    source_repr=source_repr,
                    reason="unknown_id",
                ))
                continue
            if family in _FACTUAL_FAMILIES:
                if not semantic_alignment(text, resolved):
                    issues.append(CitationIssue(
                        claim_text=text[:200],
                        source_repr=source_repr,
                        reason="content_mismatch",
                    ))
    return issues


__all__ = [
    "CitationIssue",
    "semantic_alignment",
    "validate_grounded_claims",
]
