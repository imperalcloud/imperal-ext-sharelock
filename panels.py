"""
Sharelock v2 — Left panel: Cases from Nextcloud + files (recursive).

Nextcloud-first: folders in NC storage = cases.
Files listed recursively (supports nested subfolders).
"""
import asyncio
import logging
from urllib.parse import unquote, quote

import httpx
from xml.etree import ElementTree

from imperal_sdk import ui
from app import ext, _user_id, NC_URL, NC_USER, NC_PASS, NC_BASE_PATH
from cache_models import (
    NextcloudFolderListing,
    NextcloudFileListing,
    UserCasesListing,
)
import queries

log = logging.getLogger("sharelock-v2.panels")

# Circuit-breaker timeouts for UX paths. When Cases API or Nextcloud is
# overloaded (heavy session-worker analysis traffic), panel renders must
# fail fast and serve stale-cached data rather than block until Auth GW
# fast-RPC deadline expires (which then falls back to Temporal — the
# 4-attempt retry + asyncio.CancelledError path the user observed).
_FAST_TIMEOUT_FOLDERS = 5.0   # NC PROPFIND Depth:1 — folder list
_FAST_TIMEOUT_FILES = 8.0     # NC PROPFIND Depth:infinity — file recursion
_FAST_TIMEOUT_CASES = 5.0     # Cases API GET /cases?user_id=
_TTL_NC_FOLDERS = 60          # folder set rarely changes
_TTL_NC_FILES = 30            # files change during ingestion
_TTL_USER_CASES = 30          # case status flips during analysis
_MAX_CACHED_FILES = 100       # cap to stay under 64KB cache value cap
_MAX_CACHED_CASES = 100

# Fields the panel actually reads from a case dict. Cases API
# `/cases?user_id=` returns FULL case objects with embedded `files[]`
# (potentially thousands of entries per case). Caching the thin shape
# keeps us safely under I-CACHE-VALUE-SIZE-CAP-64KB even with 100 cases.
_CASE_THIN_KEYS = (
    "id", "name", "agency_id", "analysis_status", "analysis_version",
    "active_run_id", "analysis_updated_at", "confidence_score_current",
    "confidence_score_potential", "status", "created_at",
)


def _thin_case(c: dict) -> dict:
    """Strip a case dict to fields the panel renders. Drops `files`,
    `description`, and any other large nested data before caching."""
    if not isinstance(c, dict):
        return {}
    return {k: c.get(k) for k in _CASE_THIN_KEYS if k in c}


async def _cached_nc_folders(ctx) -> list[str]:
    """Fast NC folder list with stale-cache fallback.

    Tries PROPFIND with _FAST_TIMEOUT_FOLDERS; on timeout/error returns
    last cached value. TTL _TTL_NC_FOLDERS. Returns [] if neither live
    nor cache is available.
    """
    key = "nc_folders:default"
    cached: list[str] = []
    try:
        c = await ctx.cache.get(key, NextcloudFolderListing)
        if c is not None:
            cached = list(c.folders)
    except Exception as e:
        log.debug(f"nc_folders cache read failed: {e}")

    try:
        live = await asyncio.wait_for(_list_nc_folders(),
                                       timeout=_FAST_TIMEOUT_FOLDERS)
        try:
            await ctx.cache.set(key,
                                NextcloudFolderListing(folders=list(live)),
                                ttl_seconds=_TTL_NC_FOLDERS)
        except Exception as e:
            log.debug(f"nc_folders cache write failed: {e}")
        return live
    except asyncio.TimeoutError:
        log.warning(f"nc_folders timeout {_FAST_TIMEOUT_FOLDERS}s; "
                    f"serving {'stale' if cached else 'empty'}")
        return cached
    except Exception as e:
        log.warning(f"nc_folders fetch failed: {e}; "
                    f"serving {'stale' if cached else 'empty'}")
        return cached


async def _cached_nc_files(ctx, folder: str) -> list[dict]:
    """Fast NC recursive file list with stale-cache fallback (per folder)."""
    if not folder:
        return []
    # Sanitise folder name into safe key suffix per CacheClient I-CACHE-KEY-SAFETY.
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in folder)[:80]
    key = f"nc_files:{safe}"
    cached: list[dict] = []
    try:
        c = await ctx.cache.get(key, NextcloudFileListing)
        if c is not None:
            cached = list(c.files)
    except Exception as e:
        log.debug(f"nc_files cache read failed key={key}: {e}")

    try:
        live = await asyncio.wait_for(_list_nc_files_recursive(folder),
                                       timeout=_FAST_TIMEOUT_FILES)
        # Cap at _MAX_CACHED_FILES + thin projection (drop `name` —
        # it's just basename of `path`) to stay under
        # I-CACHE-VALUE-SIZE-CAP-64KB even with deep WhatsApp-style paths.
        thin = [
            {"path": (f.get("path") or "")[:300], "size": int(f.get("size") or 0)}
            for f in list(live)[:_MAX_CACHED_FILES]
        ]
        try:
            await ctx.cache.set(key,
                                NextcloudFileListing(files=thin),
                                ttl_seconds=_TTL_NC_FILES)
        except Exception as e:
            log.debug(f"nc_files cache write failed key={key}: {e}")
        return live  # render full live list, only cache is thin
    except asyncio.TimeoutError:
        log.warning(f"nc_files folder={folder!r} timeout "
                    f"{_FAST_TIMEOUT_FILES}s; serving "
                    f"{'stale' if cached else 'empty'}")
        return cached
    except Exception as e:
        log.warning(f"nc_files folder={folder!r} fetch failed: {e}; "
                    f"serving {'stale' if cached else 'empty'}")
        return cached


async def _cached_user_cases(ctx, user_id: str) -> list[dict]:
    """Fast `queries.get_cases(user_id)` with stale-cache fallback."""
    safe_uid = "".join(ch if ch.isalnum() or ch in "_-" else "_"
                       for ch in (user_id or ""))[:80]
    key = f"user_cases:{safe_uid or 'anon'}"
    cached: list[dict] = []
    try:
        c = await ctx.cache.get(key, UserCasesListing)
        if c is not None:
            cached = list(c.cases)
    except Exception as e:
        log.debug(f"user_cases cache read failed: {e}")

    try:
        live = await asyncio.wait_for(queries.get_cases(user_id),
                                       timeout=_FAST_TIMEOUT_CASES)
        # Cache the THIN projection (no embedded `files[]` arrays) to
        # stay under I-CACHE-VALUE-SIZE-CAP-64KB even with 100 cases.
        # Live render gets the full payload.
        thin = [_thin_case(c) for c in list(live)[:_MAX_CACHED_CASES]]
        try:
            await ctx.cache.set(key,
                                UserCasesListing(cases=thin),
                                ttl_seconds=_TTL_USER_CASES)
        except Exception as e:
            log.debug(f"user_cases cache write failed: {e}")
        return live
    except asyncio.TimeoutError:
        log.warning(f"user_cases user={user_id} timeout "
                    f"{_FAST_TIMEOUT_CASES}s; serving "
                    f"{'stale' if cached else 'empty'}")
        return cached
    except Exception as e:
        log.warning(f"user_cases user={user_id} fetch failed: {e}; "
                    f"serving {'stale' if cached else 'empty'}")
        return cached


def _dav_url(path: str = "") -> str:
    return f"{NC_URL}/remote.php/dav/files/{NC_USER}{NC_BASE_PATH}{quote(path, safe='/')}"


def _propfind_body():
    return '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/><d:resourcetype/></d:prop></d:propfind>'


async def _list_nc_folders() -> list[str]:
    """Top-level folders = cases."""
    if not NC_URL or not NC_USER:
        return []
    url = _dav_url("") + "/"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.request("PROPFIND", url, auth=httpx.BasicAuth(NC_USER, NC_PASS),
                                headers={"Depth": "1", "Content-Type": "application/xml"},
                                content=_propfind_body())
            if r.status_code >= 300:
                return []
        ns = {"d": "DAV:"}
        root = ElementTree.fromstring(r.text)
        folders = []
        for resp in root.findall("d:response", ns):
            href = unquote(resp.findtext("d:href", "", ns)).rstrip("/")
            props = resp.find("d:propstat/d:prop", ns)
            if props is None:
                continue
            if props.find("d:resourcetype/d:collection", ns) is None:
                continue
            name = href.split("/")[-1]
            # Skip the root folder itself
            base = NC_BASE_PATH.strip("/").split("/")[-1]
            if name and name != base:
                folders.append(name)
        return folders
    except Exception as e:
        log.warning(f"NC folder list failed: {e}")
        return []


async def _list_nc_files_recursive(folder: str) -> list[dict]:
    """All files in folder recursively."""
    if not NC_URL or not NC_USER:
        return []
    url = _dav_url(folder) + "/"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request("PROPFIND", url, auth=httpx.BasicAuth(NC_USER, NC_PASS),
                                headers={"Depth": "infinity", "Content-Type": "application/xml"},
                                content=_propfind_body())
            if r.status_code >= 300:
                return []
        ns = {"d": "DAV:"}
        root = ElementTree.fromstring(r.text)
        # Decode the base href for comparison
        base_decoded = unquote(url.split(NC_URL)[-1]).rstrip("/")
        files = []
        for resp in root.findall("d:response", ns):
            raw_href = resp.findtext("d:href", "", ns).rstrip("/")
            decoded_href = unquote(raw_href)
            if decoded_href == base_decoded:
                continue
            props = resp.find("d:propstat/d:prop", ns)
            if props is None:
                continue
            if props.find("d:resourcetype/d:collection", ns) is not None:
                continue
            size = int(props.findtext("d:getcontentlength", "0", ns) or 0)
            # Relative path from case folder
            rel = decoded_href.replace(base_decoded + "/", "")
            files.append({"name": rel.split("/")[-1], "path": rel, "size": size})
        return files
    except Exception as e:
        log.warning(f"NC recursive list '{folder}' failed: {e}")
        return []


@ext.panel("sidebar", slot="left", title="Sharelock", icon="shield",
           default_width=300, min_width=240, max_width=400)
async def panel_sidebar(ctx, section: str = "", **kwargs):
    """Left panel: Nextcloud folders as cases + recursive file list."""
    user_id = _user_id(ctx)
    active_folder = section or ""

    # ── Cases from Nextcloud ──────────────────────────────────────────────
    nc_folders = await _cached_nc_folders(ctx)

    # Cached fast-or-stale path — Cases API may be overloaded by
    # session-worker analysis traffic; panel must not block.
    api_cases = await _cached_user_cases(ctx, user_id)
    api_by_name = {c.get("name", "").strip(): c for c in api_cases}

    case_items = []
    for folder in nc_folders:
        api_case = api_by_name.get(folder, {})
        status = api_case.get("analysis_status") or "new"
        color = "green" if status == "completed" else "yellow" if status == "running" else "gray"
        case_items.append(ui.ListItem(
            id=folder, title=folder, subtitle=f"Analysis: {status}",
            selected=(folder == active_folder),
            badge=ui.Badge(status, color=color),
            on_click=ui.Call("__panel__sidebar", section=folder),
        ))

    cases_section = ui.Section(
        title=f"Cases ({len(nc_folders)})",
        children=[
            ui.List(items=case_items) if case_items else ui.Text("No folders in Nextcloud."),
            ui.Button(label="+ New Case", variant="ghost",
                      on_click=ui.Call("__panel__dashboard", view="create",
                                       tab="", section="", case_id="")),
        ],
    )

    # ── Files (recursive) ─────────────────────────────────────────────────
    files_children = []
    file_count = 0
    if active_folder:
        nc_files = await _cached_nc_files(ctx, active_folder)
        file_count = len(nc_files)
        if nc_files:
            rows = []
            for f in nc_files[:100]:
                size_kb = f["size"] // 1024 if f["size"] else 0
                rows.append({"file": f["path"], "size": f"{size_kb} KB"})
            files_children.append(ui.DataTable(
                columns=[ui.DataColumn(key="file", label="File"), ui.DataColumn(key="size", label="Size")],
                rows=rows,
            ))
            if len(nc_files) > 100:
                files_children.append(ui.Text(f"... and {len(nc_files) - 100} more files"))
        else:
            files_children.append(ui.Text("Empty folder."))
    else:
        files_children.append(ui.Text("Select a case to see files."))

    files_section = ui.Section(title=f"Files ({file_count})", children=files_children)

    return ui.Stack(children=[cases_section, files_section])
