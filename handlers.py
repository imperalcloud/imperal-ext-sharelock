"""
Sharelock v2 — Core chat handlers (case lifecycle + search + conversation).

Analysis-run handlers (run/cancel/gap decisions) live in handlers_analysis.py.
Pure helpers (name validation, NC folder listing) live in validation.py.
Deterministic case-id resolution + cold-skeleton loader live in case_resolver.py.
All @chat.function tools are dispatched by hub routing.

Per I-SKELETON-LLM-ONLY (SDK v1.6.0): case-summary payloads are read from
``ctx.cache`` (key ``case_summary``, model ``CaseSummary``) with a short
ttl so chat narration always sees fresh counts. The skeleton itself stays
as the classifier-envelope writer (scalar summary only).
"""
import logging

from pydantic import BaseModel, Field

from app import chat, _user_id, _user_agency
from imperal_sdk.chat import ActionResult
import queries
from queries import CasesAPIError
import chat as chat_engine
from case_resolver import resolve_case_id, load_case_data_from_api
from cache_models import CaseSummary
from files import create_backend
from validation import validate_case_name, folder_exists, list_top_folders
from models import (
    CaseChatResponse, CaseListResponse, DocSearchResponse,
    CreateCaseResponse, SyncCasesResponse,
)

log = logging.getLogger("sharelock-v2.handlers")


# ── Parameter Models ──────────────────────────────────────────────────────────


class EmptyParams(BaseModel):
    """Federal V17 placeholder for handlers that take no parameters.

    `@chat.function` MUST declare a Pydantic params model — this is the
    canonical empty model reused by read-only handlers (list_*, sync_*,
    *_health) that don't need any input.
    """
    pass


class CaseChatParams(BaseModel):
    message: str = Field("", description=(
        "User's VERBATIM message about the case. Pass EXACTLY as user typed "
        "— preserve case names ('Test Files', 'Alex Case 1'), case ids "
        "('#3812', 'дело 17'), and original language (RU/EN). The handler "
        "does deterministic case-name and id matching against this string; "
        "paraphrasing or translating breaks resolution and silently drops "
        "the user into the case-list fallback."
    ))


class CreateCaseParams(BaseModel):
    name: str = Field(..., description=(
        "Name for the new investigation case. CRITICAL: pass VERBATIM as the "
        "user typed it — preserve exact wording, capitalisation, and "
        "original language (RU/EN). Do NOT paraphrase, translate, "
        "sanitise, or auto-title. Downstream validation enforces "
        "uniqueness against the user's exact string; rephrased names "
        "silently create duplicates."
    ))
    description: str = Field("", description=(
        "Case description. CRITICAL: pass VERBATIM in the user's original "
        "language. Do NOT paraphrase, translate, or expand. Empty string "
        "if the user did not provide one."
    ))


class SearchDocsParams(BaseModel):
    query: str = Field(..., description=(
        "Search term (entity name, amount, date, phrase). CRITICAL: pass "
        "VERBATIM as the user typed it — preserve exact spelling, "
        "punctuation, amounts, and original language. Do NOT paraphrase, "
        "translate, normalise, or auto-correct. The Cases API does exact "
        "substring matching; rephrased queries return empty results."
    ))
    case_id: int = Field(0, description="Case ID (0 = active case)")


# ── Storage ───────────────────────────────────────────────────────────────────

_storage = None


def _get_storage():
    global _storage
    if _storage is None:
        _storage = create_backend()
    return _storage


# ── Cache helpers ─────────────────────────────────────────────────────────────


async def _load_case_summary(ctx, user_id: str, case_id: int | None) -> CaseSummary:
    """Build the active-case summary for the current render via ``ctx.cache``.

    ``case_resolver.load_case_data_from_api`` owns the Cases API hit; here we
    only wrap it in the Pydantic model + route through ``ctx.cache`` so
    back-to-back chat turns on the same case reuse the fetch.
    """
    key_case = int(case_id) if case_id else 0

    async def _fetch() -> CaseSummary:
        if case_id:
            data = await load_case_data_from_api(user_id, case_id)
        else:
            # Cold path (no active case): preserve analysis_status per case
            # so case_list_response doesn't render every case as "not run,
            # 0 files" — that misled the user when they asked "о чем кейс X"
            # and the legacy LLM-wrapper rephrased the message dropping the
            # case name (live evidence 2026-05-02: Test Files with 18
            # completed runs rendered as "пустое дело без файлов и анализа").
            all_cases = (await queries.get_cases(user_id))[:20]
            data = {"cases": [
                {"id": c.get("id"),
                 "name": c.get("name", ""),
                 "analysis_status": c.get("analysis_status") or c.get("status"),
                 "file_count": c.get("file_count", 0)}
                for c in all_cases
            ]}
        # Thin projection before cache: cap files list to 20 entries.
        # I-CACHE-VALUE-SIZE-CAP-64KB rejects payloads > 64KB; case 35
        # (2655 files) serialises to ~142KB and fails the cache write,
        # which silently fell back to the cold seed payload with
        # analysis_status=None -> INTAKE state on a completed case.
        # Chat path does not consume the full files[] list anyway:
        # run_intelligence calls fetch_grounded_context() separately,
        # run_intake/case_list_response use file_count, not files.
        if "files" in data and isinstance(data["files"], list):
            full_count = len(data["files"])
            data["files"] = data["files"][:20]
            if full_count > 20 and "file_count" not in data:
                data["file_count"] = full_count
        return CaseSummary(**{k: v for k, v in data.items()
                               if k in CaseSummary.model_fields})

    return await ctx.cache.get_or_fetch(
        key=f"case_summary:{user_id}:{key_case}",
        model=CaseSummary,
        fetcher=_fetch,
        ttl_seconds=30,
    )


# ── Chat Functions ────────────────────────────────────────────────────────────


@chat.function("case_chat", action_type="read",
               chain_callable=True,  # SDK 5.0+ has no wrapper-LLM fallback; chain_callable=False causes kernel disambiguation to pick wrong tool (live trace 2026-05-27: «Расскажи о нем детально» misrouted to create_case). resolve_case_id is deterministic regardless of history; chat_engine degrades gracefully if ctx.history is empty.
               data_model=CaseChatResponse,
               description=(
                   "Chat about a forensic investigation case. "
                   "CRITICAL: pass the user's message VERBATIM in the "
                   "`message` parameter — preserve case names, ids, and "
                   "original language exactly. Do NOT paraphrase, translate, "
                   "or summarize."
               ))
async def case_chat(ctx, params: CaseChatParams) -> ActionResult:
    """Main conversational interface — state machine dispatches modes.

    Case-id resolution is fully delegated to ``resolve_case_id`` (federal
    rigor: deterministic, no LLM). See case_resolver.py for the order.

    NOTE on panel→chat handoff: pre-SDK 1.6.0 the Panel shell wrote the
    current ``case_id`` into ``ctx.skeleton_data["_context"]`` so case_chat
    could anchor to the user's visible tab. That channel is gone under
    I-SKELETON-LLM-ONLY; until a dedicated panel→chat context channel
    lands we rely on resolve_case_id's deterministic fallbacks (regex id
    in message → cached active-case hint → unique name match → single-case
    fallback). Panel users should mention the case name or id explicitly.

    Verbatim-message contract (v3.1.3, 2026-05-13): kernel typed dispatch
    + I-CHAT-FUNCTION-VERBATIM-PARAMS + classifier action_plan extension
    for reads guarantee that ``params.message`` is the user's RAW turn,
    not a wrapper-LLM paraphrase. The earlier raw_message recovery hack
    (which walked ctx.history to recover the user's actual phrasing) is
    removed — handlers can trust their typed args. Wrapper-LLM only
    reaches case_chat for conversational catch-all where the classifier
    intentionally left action_plan=null; that path also preserves
    verbatim per the decorator-description + system_prompt rule #8.
    """
    user_id = _user_id(ctx)
    message = params.message

    # Seed the active-case hint from cache (kept by skeleton refresh). If
    # the cache is cold the first call populates it via the Cases API.
    try:
        seed_summary = await _load_case_summary(ctx, user_id, None)
    except Exception as e:
        log.warning(f"case_chat: initial summary cache load failed: {e}")
        seed_summary = CaseSummary()
    skeleton_case_id = seed_summary.active_case_id
    panel_case_id = None  # See docstring — reinstated once panel→chat ctx lands.

    # SDK 5.0+ typed dispatch passes ctx.history; pronoun follow-ups
    # («о нем», «расскажи дальше») rely on scanning recent USER turns for
    # the case the user was discussing. Live trace 2026-05-27 added this
    # path because the legacy wrapper-LLM history channel is gone.
    _history = getattr(ctx, "history", None) or []
    case_id, resolution = await resolve_case_id(
        user_id, message, panel_case_id, skeleton_case_id,
        history=_history,
    )

    # Pull the per-case summary (separate cache slot per case_id).
    # FIX 2026-05-02: ALWAYS refresh per-case data when case_id is resolved.
    # The previous conditional skipped refresh when skeleton_case_id ==
    # resolved case_id — but `seed_summary` is the cold-path payload built
    # from `_load_case_summary(ctx, user_id, None)` which only has the
    # bare cases list (no analysis_status / file_count / files for any
    # specific case). Skipping refresh fed cold data with
    # `analysis_status=None` into `resolve_state` → INTAKE → user saw
    # "0 файлов, анализ запущен но в ожидании" even though the case had
    # 2655 files and a completed analysis. The cache layer
    # (`_load_case_summary`) has its own TTL (30s) so this isn\'t a
    # per-message refetch — back-to-back turns still hit ctx.cache.
    case_summary = seed_summary
    if case_id:
        try:
            case_summary = await _load_case_summary(ctx, user_id, case_id)
        except Exception as e:
            log.warning(f"case_chat: summary reload for case {case_id} failed: {e}")

    # Downstream chat_engine helpers expect the dict shape of the legacy
    # skeleton payload — model_dump() gives us exactly that.
    case_data = case_summary.model_dump()

    analysis_status = case_data.get("analysis_status")
    state = chat_engine.resolve_state(case_id, analysis_status)
    log.info(
        f"case_chat: state={state} case_id={case_id} analysis={analysis_status} "
        f"resolution={resolution} user={user_id}"
    )

    if state == "STATUS":
        return ActionResult.success(data={"state": "status"},
                                    summary=chat_engine.status_response())

    if state == "CASE_LIST":
        return ActionResult.success(data={"state": "case_list"},
                                    summary=chat_engine.case_list_response(case_data))

    # Federal-grade transparency: when we resolved from the user's message,
    # tell the LLM the grounding source so it can confirm if the user asked
    # about a different case.
    resolution_note = None
    if resolution in ("regex_id", "name_match"):
        case_name = case_data.get("case_name") or f"Case-{case_id}"
        resolution_note = (
            f"NOTE: Case resolved from user message → case_id={case_id} "
            f"(path={resolution}, name={case_name!r}). Confirm if this is "
            f"not the case the user intended."
        )

    if state == "INTELLIGENCE":
        text = await chat_engine.run_intelligence(
            message, ctx.history, case_data, case_id, ctx,
            resolution_note=resolution_note,
        )
        return ActionResult.success(data={"state": "intelligence"}, summary=text)

    text = await chat_engine.run_intake(
        message, ctx.history, case_data, case_id, ctx,
        resolution_note=resolution_note,
    )
    return ActionResult.success(data={"state": "intake"}, summary=text)


@chat.function("create_case", action_type="write",
               effects=["create:case", "create:folder"],
               data_model=CreateCaseResponse,
               description=(
                   "Create a new investigation case. CRITICAL: pass "
                   "user-supplied `name` and `description` VERBATIM in the "
                   "original language (RU/EN). Do NOT paraphrase, "
                   "translate, sanitise, or auto-title. Federal anti-"
                   "hallucination invariant I-CHAT-FUNCTION-VERBATIM-PARAMS "
                   "applies — rephrased input silently breaks dedupe."
               ))
async def fn_create_case(ctx, params: CreateCaseParams) -> ActionResult:
    """Create case in Cases API + folder in Nextcloud (B7: validate + dedupe)."""
    user_id = _user_id(ctx)
    clean_name, err = validate_case_name(params.name)
    if err:
        return ActionResult.error(err, retryable=False)

    if await folder_exists(clean_name):
        return ActionResult.error(
            f"A Nextcloud folder named '{clean_name}' already exists. "
            "Use sync_cases to register it, or choose a different name.",
            retryable=False,
        )

    try:
        agency = _user_agency(ctx)
        result = await queries.create_case(user_id, clean_name,
                                           params.description or "",
                                           agency_id=agency)
        case_id = result.get("id", "?")

        try:
            storage = _get_storage()
            await storage.mkdir(clean_name)
            log.info(f"Created Nextcloud folder: {clean_name}")
        except Exception as e:
            log.warning(f"NC folder creation failed for case {case_id} "
                        f"({clean_name}): {e}")

        return ActionResult.success(
            data={"case_id": case_id, "name": clean_name},
            summary=f"Case **{clean_name}** created (ID: {case_id}). "
                    f"Upload documents to the Nextcloud folder or use the file manager.",
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Failed to create case: {e.detail or e}",
                                  retryable=False)
    except Exception as e:
        return ActionResult.error(f"Failed to create case: {e}")


@chat.function("sync_cases", action_type="write",
               effects=["create:case"],
               data_model=SyncCasesResponse,
               description="Sync cases from Nextcloud folders — create cases for new folders")
async def fn_sync_cases(ctx, params: EmptyParams) -> ActionResult:
    """Scan Nextcloud for folders, create cases for any that don't exist yet."""
    user_id = _user_id(ctx)
    try:
        agency = _user_agency(ctx)
        existing_cases = await queries.get_cases(user_id, agency_id=agency)
        existing_names = {c.get("name", "").strip().lower()
                          for c in existing_cases}

        folders = await list_top_folders()

        created, skipped = [], []
        for folder_name in folders:
            if folder_name.strip().lower() in existing_names:
                skipped.append(folder_name)
                continue
            try:
                await queries.create_case(user_id, folder_name,
                                          f"Synced from Nextcloud: {folder_name}",
                                          agency_id=agency)
                created.append(folder_name)
            except Exception as e:
                log.warning(f"Failed to create case for folder '{folder_name}': {e}")

        summary_parts = []
        if created:
            summary_parts.append(f"Created {len(created)} new case(s): "
                                 f"{', '.join(created)}")
        if skipped:
            summary_parts.append(f"Already synced: {len(skipped)} case(s)")
        if not folders:
            summary_parts.append("No folders found in Nextcloud storage.")

        return ActionResult.success(
            data={"created": created, "skipped": skipped,
                  "total_folders": len(folders)},
            summary="\n".join(summary_parts) if summary_parts else "Sync complete.",
        )
    except Exception as e:
        return ActionResult.error(f"Sync failed: {e}")


@chat.function("list_cases", action_type="read",
               data_model=CaseListResponse,
               description="List all investigation cases")
async def fn_list_cases(ctx, params: EmptyParams) -> ActionResult:
    """List all cases for the current user."""
    user_id = _user_id(ctx)
    try:
        agency = _user_agency(ctx)
        cases = await queries.get_cases(user_id, agency_id=agency)
        lines = []
        for c in cases[:20]:
            a_st = c.get("analysis_status") or c.get("status") or "not run"
            lines.append(f"- **{c.get('name', '?')}** (ID: {c.get('id')}) — {a_st}")
        summary = "\n".join(lines) if lines else "No cases found."
        return ActionResult.success(
            data={"cases": cases, "count": len(cases)},
            summary=summary,
        )
    except Exception as e:
        return ActionResult.error(f"Failed to list cases: {e}")


@chat.function("search_docs", action_type="read",
               data_model=DocSearchResponse,
               description=(
                   "Search case documents for entities, amounts, dates, "
                   "or phrases. CRITICAL: pass the user-supplied `query` "
                   "VERBATIM — preserve exact spelling, punctuation, "
                   "amounts, and original language. Do NOT paraphrase, "
                   "translate, normalise, or auto-correct. The Cases API "
                   "performs exact substring matching."
               ))
async def fn_search_docs(ctx, params: SearchDocsParams) -> ActionResult:
    """Search case documents via Cases API."""
    case_id = params.case_id
    if not case_id:
        user_id = _user_id(ctx)
        try:
            summary = await _load_case_summary(ctx, user_id, None)
            case_id = summary.active_case_id
        except Exception as e:
            log.warning(f"search_docs: case_summary cache load failed: {e}")
            case_id = None
    if not case_id:
        return ActionResult.error("No active case. Select a case first.")
    try:
        import httpx, os as _os
        # Read directly from env to avoid cross-extension sys.modules["app"]
        # pollution (this local import fails when another extension's app.py
        # is currently registered as sys.modules["app"]).
        CASES_API_URL = _os.environ.get("CASES_API_URL", "http://66.78.41.10:8096")
        CASES_API_KEY = _os.environ.get("CASES_API_KEY", "")
        async with httpx.AsyncClient(timeout=60.0) as c:
            _agency = _user_agency(ctx)
            _headers = {"x-api-key": CASES_API_KEY,
                        "Content-Type": "application/json"}
            if _agency:
                _headers["X-Imperal-Agency-ID"] = _agency
            r = await c.post(
                f"{CASES_API_URL}/cases/{case_id}/search",
                headers=_headers,
                json={"query": params.query},
            )
            if r.status_code == 404:
                return ActionResult.success(
                    data={"results": []},
                    summary=f"No results found for '{params.query}' in case documents.",
                )
            r.raise_for_status()
            results = r.json()
        count = len(results) if isinstance(results, list) else results.get("count", 0)
        return ActionResult.success(
            data=results,
            summary=f"Found {count} result(s) for '{params.query}' in case documents.",
        )
    except Exception as e:
        return ActionResult.error(f"Search failed: {e}")
