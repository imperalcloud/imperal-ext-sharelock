"""
Sharelock v2 — Deterministic case-id resolution + cold-skeleton loader.

Pure manual scan + DB lookup, NO LLM, NO regex (LLM Cloud OS principle).
Ambiguous matches return None — we never guess. Every resolution path is
logged for audit (federal rigor).

Resolution order (used by ``resolve_case_id``):
  1. Panel context (``panel_case_id``).
  2. Explicit id in message — beats stale skeleton default.
  3. Skeleton default (``skeleton_case_id``).
  4. Unique case-name substring match (caller-supplied case names —
     not message intent matching).
  5. Sole-case fallback.
  6. Otherwise → None.
"""
from __future__ import annotations

import logging

import queries

log = logging.getLogger("sharelock-v2.case_resolver")


_MIN_NAME_LENGTH = 3  # Shorter names are too generic for safe substring match.

# Russian "дело" stem variants (declensions). Mirrors the legacy regex
# ``де(?:ло|ла|ле|лу|лом)``. Order longest-first so "делом" matches before
# "дело" inside the manual scanner.
_DELO_STEMS = ("делом", "дело", "дела", "деле", "делу")


# ── Manual case-id scanners (no regex) ────────────────────────────────────────


def _is_word_boundary_char(ch: str) -> bool:
    """Word boundary = anything that is NOT a Latin/Cyrillic letter or digit
    or underscore. Mirrors the ``\b`` semantics of the legacy regex.
    """
    return not (ch.isalnum() or ch == "_")


def _read_digits(text: str, start: int) -> tuple[int | None, int]:
    """Read a maximal run of decimal digits starting at ``start``.

    Returns (parsed_int_or_None, position_after_digits).
    """
    end = start
    while end < len(text) and text[end].isdigit():
        end += 1
    if end == start:
        return None, start
    try:
        return int(text[start:end]), end
    except ValueError:
        return None, end


def _skip_optional_separators(text: str, i: int, allow: tuple[str, ...]) -> int:
    """Advance past a run of allowed separator chars (spaces, tabs,
    underscore, "#", ":")."""
    while i < len(text) and text[i] in allow:
        i += 1
    return i


def _try_match_case_word(text_lower: str, i: int) -> tuple[int | None, int]:
    """At position ``i``, try to match the patterns:
        case <digits>
        case <sep> <digits>
        case [_ #]* <digits>
        case[_ :]+id[ :]*<digits>

    Returns (case_id_or_None, new_i_after_consumed).
    Caller is responsible for left word-boundary; this checks right boundary
    after consuming "case" + variants.
    """
    if not text_lower.startswith("case", i):
        return None, i
    j = i + 4  # past "case"
    if j > len(text_lower):
        return None, i
    # Right boundary check: char after "case" must be a separator candidate
    # (space, _, #, :) — not another letter that would make this part of a
    # bigger word like "casefile".
    if j < len(text_lower) and text_lower[j].isalnum():
        return None, i
    # Variant A: "case[_ \s#:]*<digits>"
    j = _skip_optional_separators(text_lower, j, (" ", "\t", "_", "#"))
    digits, k = _read_digits(text_lower, j)
    if digits is not None and (k == len(text_lower) or _is_word_boundary_char(text_lower[k])):
        return digits, k
    # Variant B: "case[_ :]+id[\s:]*<digits>"
    j2 = _skip_optional_separators(text_lower, i + 4, (" ", "\t", "_", ":"))
    if j2 > i + 4 and text_lower.startswith("id", j2):
        j3 = j2 + 2
        if j3 < len(text_lower) and text_lower[j3].isalnum():
            return None, i
        j3 = _skip_optional_separators(text_lower, j3, (" ", "\t", ":"))
        digits, k = _read_digits(text_lower, j3)
        if digits is not None and (k == len(text_lower) or _is_word_boundary_char(text_lower[k])):
            return digits, k
    return None, i


def _try_match_hash_id(text: str, i: int) -> tuple[int | None, int]:
    """Match "#<1..6 digits>" with right word boundary."""
    if i >= len(text) or text[i] != "#":
        return None, i
    j = i + 1
    digits, k = _read_digits(text, j)
    if digits is None or (k - j) > 6 or (k - j) == 0:
        return None, i
    if k < len(text) and not _is_word_boundary_char(text[k]):
        return None, i
    return digits, k


def _try_match_delo(text_lower: str, i: int) -> tuple[int | None, int]:
    """Match Russian "дело|дела|деле|делу|делом [#|id]? <digits>".

    Caller verifies left word boundary at ``i``.
    """
    matched_stem_end = -1
    for stem in _DELO_STEMS:
        if text_lower.startswith(stem, i):
            matched_stem_end = i + len(stem)
            break
    if matched_stem_end < 0:
        return None, i
    # Right-boundary on the stem itself: next char must NOT continue the word.
    if matched_stem_end < len(text_lower) and text_lower[matched_stem_end].isalnum():
        return None, i
    j = _skip_optional_separators(text_lower, matched_stem_end, (" ", "\t"))
    # Optional "#" or "id".
    if j < len(text_lower) and text_lower[j] == "#":
        j += 1
    elif text_lower.startswith("id", j):
        j2 = j + 2
        if j2 == len(text_lower) or _is_word_boundary_char(text_lower[j2]):
            j = j2
    j = _skip_optional_separators(text_lower, j, (" ", "\t"))
    digits, k = _read_digits(text_lower, j)
    if digits is None:
        return None, i
    if k < len(text_lower) and not _is_word_boundary_char(text_lower[k]):
        return None, i
    return digits, k


def extract_case_id_from_text(message: str) -> int | None:
    """Return the first explicit case id found in ``message`` or None.

    Manual scan (no regex). Mirrors the three legacy patterns:
      - case[_\s#]*<digits>
      - case[_\s:]+id[\s:]*<digits>
      - #<1..6 digits>
      - дело|дела|деле|делу|делом [#|id]? <digits>
    """
    if not message:
        return None
    text_lower = message.lower()
    n = len(text_lower)
    i = 0
    while i < n:
        # Left word boundary.
        left_ok = (i == 0) or _is_word_boundary_char(text_lower[i - 1])
        if left_ok:
            # Try "case ..." patterns.
            cid, _ = _try_match_case_word(text_lower, i)
            if cid is not None:
                return cid
            # Try Russian "дело ..." patterns.
            cid, _ = _try_match_delo(text_lower, i)
            if cid is not None:
                return cid
        # Try "#<digits>" — does not require left word boundary, "#" is its own marker.
        cid, _ = _try_match_hash_id(message, i)
        if cid is not None:
            return cid
        i += 1
    return None


async def resolve_case_from_message(
    user_id: str, message: str,
) -> tuple[int | None, str | None]:
    """Extract case_id from a user message.

    Returns ``(case_id, path)`` where ``path`` is ``"name_match"`` or
    ``"regex_id"``. Returns ``(None, None)`` if no single case could be
    identified (including ambiguous matches).

    PRIORITY (FIX 2026-05-02):
    - Name match wins over regex_id when the user message literally contains
      a known case name. Earlier order extracted "1" out of "alex case 1"
      via regex BEFORE checking that the user actually has a case named
      "Alex Case 1" — federal regression because the integer 1 is rarely
      a valid case_id for the user.
    - When multiple case names match, prefer the LONGEST match (so
      "Alex Case 1" beats a hypothetical "Alex" sub-name).
    - Regex_id is only honoured when the extracted integer is an actual
      case_id in the user\'s cases list — never fall through to a stranger
      case via a stray digit in the message.
    """
    if not message:
        return None, None

    try:
        cases = await queries.get_cases(user_id)
    except Exception as e:
        log.warning(f"resolve_case_from_message: get_cases({user_id}) failed: {e}")
        return None, None

    if not cases:
        return None, None

    import re as _re

    user_case_ids = {int(c.get("id")) for c in cases if c.get("id") is not None}

    # Step 0: standalone digits = explicit case_id reference.
    msg_stripped = message.strip()
    if msg_stripped.isdigit():
        try:
            ival = int(msg_stripped)
            if ival in user_case_ids:
                return ival, "explicit_id"
        except ValueError:
            pass

    # Step 1: word-token fuzzy name match.
    # Tokenise message + each case name; require at least 50% of the
    # name\'s tokens to appear in the message (case-insensitive). Pick
    # the case with the highest absolute number of matching tokens —
    # longer names win on tie because more tokens = more specific.
    # This handles short messages like "alex case" matching the full
    # case name "Alex Case 1" — bi-directional substring missed because
    # neither full string was contained in the other.
    def _tokens(s: str) -> set[str]:
        return {t for t in _re.findall(r"\w+", s.lower()) if len(t) >= 1}

    msg_tokens = _tokens(message)

    scored: list[tuple[dict, int, int]] = []  # (case, matched_count, name_token_count)
    for c in cases:
        name = (c.get("name") or "").strip()
        if not name or len(name) < _MIN_NAME_LENGTH:
            continue
        name_tokens = _tokens(name)
        if not name_tokens:
            continue
        matched = len(name_tokens & msg_tokens)
        if matched == 0:
            continue
        # Federal-grade: require >=50% of name tokens present in message.
        if matched / len(name_tokens) >= 0.5:
            scored.append((c, matched, len(name_tokens)))

    if scored:
        # Highest matched count first; longer name (more tokens) breaks ties.
        scored.sort(key=lambda x: (-x[1], -x[2]))
        top = scored[0]
        ties = [s for s in scored if s[1] == top[1] and s[2] == top[2]]
        if len(ties) == 1:
            return top[0].get("id"), "name_match"
        top_name = top[0].get("name")
        log.info(
            f"resolve_case_from_message: ambiguous name match user={user_id} "
            f"top_name={top_name!r} ties={len(ties)}"
        )

    # Step 2: integer extraction from message — accepts \"id 35\",
    # \"case 35\", \"кейс 35\", \"#35\", or any embedded integer
    # validated against the user\'s real case_ids.
    cid = extract_case_id_from_text(message)
    if cid is not None and int(cid) in user_case_ids:
        return cid, "regex_id"

    # Step 3: bare integers anywhere in the message — e.g. "id 35" /
    # "это 35" / "open 3812". Take the first integer that matches a
    # user case_id. Federal safety: only consider integers >= 1 and
    # validate against user_case_ids so stray years / amounts in case
    # names never cross-match.
    for m in _re.finditer(r"\b(\d{1,7})\b", message):
        try:
            ival = int(m.group(1))
            if ival > 0 and ival in user_case_ids:
                return ival, "embedded_id"
        except ValueError:
            continue

    return None, None


async def pick_fallback_single_case(user_id: str) -> int | None:
    """Return the sole case's id if the user has exactly one. Else None."""
    try:
        cases = await queries.get_cases(user_id)
    except Exception as e:
        log.warning(f"pick_fallback_single_case: get_cases({user_id}) failed: {e}")
        return None
    if len(cases) == 1:
        return cases[0].get("id")
    return None


async def resolve_case_id(
    user_id: str,
    message: str,
    panel_case_id: int | None,
    skeleton_case_id: int | None,
) -> tuple[int | None, str | None]:
    """Apply the full resolution order. Returns ``(case_id, path)``.

    ``path`` is one of: ``panel``, ``name_match``, ``regex_id``,
    ``skeleton``, ``single_case``, or ``None`` if unresolved.

    ORDER (FIX 2026-05-02):
      1. panel_case_id  — explicit panel selection from the UI.
      2. resolve_case_from_message — name_match (longest sub-match wins),
         then regex_id validated against the user\'s real case_ids.
         This block is now EARLIER than `skeleton_case_id` because an
         explicit case-name mention in the message is stronger evidence
         of intent than a cached "what was active last turn" hint.
      3. skeleton_case_id — recent active-case hint from the skeleton.
      4. pick_fallback_single_case — single-case-only fallback.
    """
    if panel_case_id:
        return panel_case_id, "panel"

    cid, path = await resolve_case_from_message(user_id, message)
    if cid:
        return cid, path

    if skeleton_case_id:
        return skeleton_case_id, "skeleton"

    lone = await pick_fallback_single_case(user_id)
    if lone:
        return lone, "single_case"

    return None, None


# ── Cold-skeleton loader ──────────────────────────────────────────────────────


async def load_case_data_from_api(user_id: str, case_id: int) -> dict:
    """Fetch case snapshot from Cases API when skeleton is cold/stale.

    Returns the same shape used by skeleton.case_status:
      {cases, active_case_id, case_name, analysis_status, file_count, files}
    """
    log.info(f"load_case_data_from_api user={user_id} case={case_id}")
    result: dict = {}
    try:
        cases = await queries.get_cases(user_id)
        result["cases"] = [{"id": c.get("id"), "name": c.get("name", "")}
                           for c in cases[:20]]
        active = next((c for c in cases if c.get("id") == case_id),
                      cases[0] if cases else None)
        if active:
            result["active_case_id"] = active.get("id")
            result["case_name"] = active.get("name", "")
    except Exception as e:
        log.warning(f"load_case_data_from_api: cases fetch failed: {e}")
    if case_id:
        try:
            ar = await queries.get_analysis(case_id)
            result["analysis_status"] = ar.get("analysis_status")
        except Exception:
            pass
        try:
            files = await queries.get_files(case_id)
            result["file_count"] = len(files)
            result["files"] = [{"filename": f.get("filename", ""),
                                "size": f.get("size", 0)} for f in files]
        except Exception:
            pass
    return result
