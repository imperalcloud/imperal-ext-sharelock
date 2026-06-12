"""
Sharelock v2 — Input validation + Nextcloud helpers.

No Extension / chat object registration here — safe to import from handlers
or any panel module. (Imports files for the shared DTD-rejecting DAV XML
parse; module-level per the no-lazy-local-imports rule.)
"""
import logging

from files import parse_dav_xml

log = logging.getLogger("sharelock-v2.validation")


# Allowed character set for case names. Manual char-class check; no regex
# (LLM Cloud OS principle).
_CASE_NAME_ALLOWED = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " _-.,()"
)


def _case_name_chars_ok(name: str) -> bool:
    """True iff every character of ``name`` is in the allowed set."""
    return all(ch in _CASE_NAME_ALLOWED for ch in name)

_RESERVED_CASE_NAMES = {
    ".", "..", "admin", "billing", "system", "imperal", "developer", "marketplace",
}


def validate_case_name(raw: str) -> tuple[str, str]:
    """Normalise + validate case name. Returns (clean_name, err_msg).

    err_msg='' means the name is acceptable. Federal rigor: reject reserved
    names, enforce length 1..100, charset letters/digits/spaces/._-,().
    """
    if raw is None:
        return "", "Case name is required."
    name = raw.strip()
    if not name:
        return "", "Case name cannot be empty."
    if len(name) > 100:
        return name, "Case name is too long (max 100 characters)."
    if name.lower() in _RESERVED_CASE_NAMES:
        return name, f"'{name}' is a reserved name. Please choose a different case name."
    if not _case_name_chars_ok(name):
        return name, ("Case name can only contain letters, digits, spaces, and the "
                      "characters _ - . , ( )")
    return name, ""


# ── Nextcloud PROPFIND helpers ────────────────────────────────────────────────


_PROPFIND_BODY = (
    '<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
    '<d:prop><d:resourcetype/></d:prop></d:propfind>'
)


async def list_top_folders(backend) -> list[str]:
    """Return the list of top-level folder names under the backend's base path.

    ``backend`` is a NextcloudWebDAV-shaped object exposing ``url``, ``user``,
    ``password`` and ``base_path`` (see files.py). Pure helper: the caller
    resolves the per-agency backend (files.get_agency_backend) and passes it
    in — no app/ctx imports here.

    Returns an empty list if the backend is not configured or any error
    occurs. The caller is responsible for deciding what to do with an
    empty list.
    """
    try:
        import httpx
        from urllib.parse import unquote
        if not backend.url or not backend.user:
            return []
        dav_url = f"{backend.url}/remote.php/dav/files/{backend.user}{backend.base_path}"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request("PROPFIND", dav_url,
                                auth=httpx.BasicAuth(backend.user, backend.password),
                                headers={"Depth": "1",
                                         "Content-Type": "application/xml"},
                                content=_PROPFIND_BODY)
            if r.status_code >= 300:
                return []
        ns = {"d": "DAV:"}
        root = parse_dav_xml(r.text)
        base = backend.base_path.strip("/").split("/")[-1]
        out: list[str] = []
        for resp in root.findall("d:response", ns):
            href = resp.findtext("d:href", "", ns)
            props = resp.find("d:propstat/d:prop", ns)
            if props is None:
                continue
            if props.find("d:resourcetype/d:collection", ns) is None:
                continue
            name = href.rstrip("/").split("/")[-1]
            if name and name != base:
                out.append(unquote(name))
        return out
    except Exception as e:
        log.warning(f"list_top_folders failed: {e}")
        return []


async def folder_exists(name: str, backend) -> bool:
    """Return True if a top-level folder with this name exists (case-insensitive).

    ``backend`` is passed through to list_top_folders (per-agency storage).
    """
    target = (name or "").strip().lower()
    if not target:
        return False
    folders = await list_top_folders(backend)
    return any(f.lower() == target for f in folders)
