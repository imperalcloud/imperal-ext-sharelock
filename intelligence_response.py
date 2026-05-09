"""
Sharelock v2 — IntelligenceResponse: structured LLM response (no regex).

The LLM in the INTELLIGENCE state returns a tool_use call against the
emit_intelligence_response schema. The kernel-style structured-output
pattern: define a Pydantic-shaped dataclass, build a strict JSON Schema
that works on Anthropic tool_use AND OpenAI strict response_format, then
parse the returned dict into the dataclass.

Why structured output:
- Sharelock is a tool/skill inside Webbee. The user must NEVER see
  internal grounding scaffolding ([CASE:status], [I2:T1], [G1], ...).
- Federal grounding still requires every factual claim be tied to a
  source ID — but that linkage lives in the `claims` array, not in the
  user-facing `prose`.
- Validators consume `claims` directly. Zero regex anywhere in the path
  (per LLM Cloud OS principle: no substring/regex matching).

Schema constraints (OAI strict + Anthropic tool_use compatible):
  - additionalProperties: false at every object level
  - all properties listed in `required` (union-null for optional fields)
  - no maxLength / pattern / format keywords
  - enums used only for closed sets

Source families mirror intelligence_tag_resolver.resolve_tag():
    CASE, RUN, TAX, INS, GRAPH, G, S, CC, I, E, A
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


_TOOL_NAME = "emit_intelligence_response"

ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]

_SOURCE_FAMILIES = (
    "CASE", "RUN", "TAX", "INS", "GRAPH",
    "G", "S", "CC", "I", "E", "A",
)


@dataclass(frozen=True, slots=True)
class CitationSource:
    """One grounded source ID, decomposed.

    Mirrors the (family, qual1, num, subtype, subnum, qual2) tuple consumed
    by intelligence_tag_resolver.resolve_tag — already structured, no regex.

    Examples:
      [CASE:status]                  -> family=CASE, qual1=status
      [INS:02_Documents:Reports]     -> family=INS, qual1=02_Documents, qual2=Reports
      [I2:T1]                        -> family=I, num=2, subtype=T, subnum=1
      [G1]                           -> family=G, num=1
      [S1:F1]                        -> family=S, num=1, subtype=F, subnum=1
      [GRAPH:total_entities]         -> family=GRAPH, qual1=total_entities
      [TAX:02_Documents:Reports]     -> family=TAX, qual1=02_Documents, qual2=Reports
    """
    family: str
    qual1: str | None = None
    num: str | None = None
    subtype: str | None = None
    subnum: str | None = None
    qual2: str | None = None


@dataclass(frozen=True, slots=True)
class GroundedClaim:
    """A factual claim and its grounded sources.

    `text` is the claim itself (audit-visible). The user-facing rendering
    is in IntelligenceResponse.prose — the LLM should write the same
    information there in natural prose without inline tags.
    """
    text: str
    sources: tuple[CitationSource, ...] = ()


@dataclass(frozen=True, slots=True)
class IntelligenceResponse:
    """Top-level response object emitted by the LLM via tool_use.

    Fields:
      prose            User-facing text. Clean — NO inline citation tags,
                       NO self-introduction, NO "Я — Webbee, ..." prefix.
                       Match the user's language for the entire response.
      claims           Per-fact grounding for the audit pipeline.
                       Every factual statement in `prose` MUST have a
                       corresponding GroundedClaim with at least one
                       valid CitationSource.
      confidence       HIGH / MEDIUM / LOW / UNKNOWN.
      unknown_fields   Fields the user asked about but were not present
                       in CASE CONTEXT. Empty tuple = full coverage.
    """
    prose: str
    claims: tuple[GroundedClaim, ...] = ()
    confidence: ConfidenceLevel = "UNKNOWN"
    unknown_fields: tuple[str, ...] = ()


# ----------------------------------------------------------------------
# JSON Schema builder — strict-mode compatible with Anthropic tool_use +
# OpenAI strict response_format. Mirrors core/intent.py:build_classifier_tool_schema
# discipline (additionalProperties=false everywhere; all props in required;
# union-null for optional values; enums for closed sets).
# ----------------------------------------------------------------------

def build_intelligence_tool_schema() -> dict:
    """Strict JSON Schema for the emit_intelligence_response tool.

    Compatible with:
      - Anthropic Claude tool_use (input_schema)
      - OpenAI strict tools (function.parameters with strict=true)
      - OpenAI json_schema response_format (schema with strict=true)
    """
    citation_source_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["family", "qual1", "num", "subtype", "subnum", "qual2"],
        "properties": {
            "family": {
                "type": "string",
                "enum": list(_SOURCE_FAMILIES),
                "description": (
                    "Source family. CASE/RUN/GRAPH carry a qual1 string. "
                    "TAX/INS carry qual1 (top-level category) + optional qual2 "
                    "(subcategory, e.g. INS qual1=02_Documents qual2=Reports). "
                    "G/S/CC/I/E/A carry a numeric `num` and optional subtype+subnum "
                    "(e.g. I num=2 subtype=T subnum=1 -> [I2:T1])."
                ),
            },
            "qual1": {
                "type": ["string", "null"],
                "description": (
                    "Primary qualifier. CASE: status/version/etc. "
                    "RUN: status/files_total/etc. INS/TAX: top-level category "
                    "(e.g. 02_Documents) or __TOTAL__. GRAPH: total_entities/etc. "
                    "Null for G/S/CC/I/E/A."
                ),
            },
            "num": {
                "type": ["string", "null"],
                "description": (
                    "Numeric index for G/S/CC/I/E/A families (string-encoded). "
                    "Null for CASE/RUN/TAX/INS/GRAPH."
                ),
            },
            "subtype": {
                "type": ["string", "null"],
                "description": (
                    "Sub-family marker for nested numeric IDs: I num=2 subtype=T "
                    "subnum=1 encodes [I2:T1]. Allowed: T (target), C (charge), "
                    "F (finding). Null for non-nested numeric tags and for "
                    "CASE/RUN/TAX/INS/GRAPH."
                ),
            },
            "subnum": {
                "type": ["string", "null"],
                "description": (
                    "Sub-index for nested numeric IDs: S num=1 subtype=F subnum=1 "
                    "encodes [S1:F1] (finding 1 of summary 1). Null otherwise."
                ),
            },
            "qual2": {
                "type": ["string", "null"],
                "description": (
                    "Secondary qualifier for TAX/INS: subcategory (e.g. Reports "
                    "for INS qual1=02_Documents qual2=Reports -> [INS:02_Documents:Reports]). "
                    "Null for all other families."
                ),
            },
        },
    }

    grounded_claim_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "sources"],
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "The factual claim, in the user's language. Audit-visible. "
                    "MUST correspond to information visible in CASE CONTEXT."
                ),
            },
            "sources": {
                "type": "array",
                "items": citation_source_schema,
                "description": (
                    "One or more grounded sources backing this claim. NEVER "
                    "empty for factual claims. Empty list is allowed only "
                    "for prose framing (e.g. 'Here is your case')."
                ),
            },
        },
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["prose", "claims", "confidence", "unknown_fields"],
        "properties": {
            "prose": {
                "type": "string",
                "description": (
                    "User-facing answer. CLEAN: no inline citation tags "
                    "([CASE:..], [I2:T1], [G1], etc), no self-introduction, "
                    "no 'I am Webbee' / 'Я — Webbee' prefixes. Match the "
                    "user's language for the entire response. ONE answer "
                    "to the CURRENT user message — do NOT batch prior turns."
                ),
            },
            "claims": {
                "type": "array",
                "items": grounded_claim_schema,
                "description": (
                    "Per-claim grounding for the audit pipeline. Every "
                    "factual statement in `prose` MUST have a matching "
                    "GroundedClaim with at least one CitationSource."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"],
                "description": (
                    "HIGH = direct quote / exact integer from a specific source. "
                    "MEDIUM = inferred from multiple sources with clear linkage. "
                    "LOW = partial information; user should verify. "
                    "UNKNOWN = not in CASE CONTEXT."
                ),
            },
            "unknown_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Fields the user asked about that were NOT in CASE CONTEXT. "
                    "Empty array = full coverage."
                ),
            },
        },
    }


# ----------------------------------------------------------------------
# Parser — dict (from tool_use args) -> IntelligenceResponse dataclass.
# Pure structural mapping. No regex, no string scanning.
# ----------------------------------------------------------------------

def _parse_source(d: Any) -> CitationSource | None:
    if not isinstance(d, dict):
        return None
    family = d.get("family")
    if family not in _SOURCE_FAMILIES:
        return None
    return CitationSource(
        family=family,
        qual1=d.get("qual1"),
        num=d.get("num"),
        subtype=d.get("subtype"),
        subnum=d.get("subnum"),
        qual2=d.get("qual2"),
    )


def _parse_claim(d: Any) -> GroundedClaim | None:
    if not isinstance(d, dict):
        return None
    text = d.get("text")
    if not isinstance(text, str):
        return None
    raw_sources = d.get("sources") or []
    sources: list[CitationSource] = []
    if isinstance(raw_sources, list):
        for s in raw_sources:
            parsed = _parse_source(s)
            if parsed is not None:
                sources.append(parsed)
    return GroundedClaim(text=text, sources=tuple(sources))


def parse_intelligence_response(args: Any) -> IntelligenceResponse | None:
    """Map tool_use args dict to IntelligenceResponse dataclass.

    Returns None if the shape is fundamentally broken (no prose).
    Missing claims/confidence/unknown_fields fall back to empty defaults
    so a partial response is still surfaced — UNKNOWN confidence flags
    that to the audit pipeline.
    """
    if not isinstance(args, dict):
        return None
    prose = args.get("prose")
    if not isinstance(prose, str):
        return None

    raw_claims = args.get("claims") or []
    claims: list[GroundedClaim] = []
    if isinstance(raw_claims, list):
        for c in raw_claims:
            parsed = _parse_claim(c)
            if parsed is not None:
                claims.append(parsed)

    confidence_raw = args.get("confidence")
    if confidence_raw not in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}:
        confidence_raw = "UNKNOWN"

    raw_unknown = args.get("unknown_fields") or []
    unknown_fields: tuple[str, ...] = ()
    if isinstance(raw_unknown, list):
        unknown_fields = tuple(s for s in raw_unknown if isinstance(s, str))

    return IntelligenceResponse(
        prose=prose,
        claims=tuple(claims),
        confidence=confidence_raw,  # type: ignore[arg-type]
        unknown_fields=unknown_fields,
    )


__all__ = [
    "_TOOL_NAME",
    "CitationSource",
    "GroundedClaim",
    "IntelligenceResponse",
    "build_intelligence_tool_schema",
    "parse_intelligence_response",
]
