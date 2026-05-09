"""
Sharelock v2 — Input validation + Nextcloud helpers.

Pure helpers: no Extension / chat imports. Safe to import from handlers or
any panel module.
"""
import logging

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


async def list_top_folders() -> list[str]:
    """Return the list of top-level folder names under NC_BASE_PATH.

    Returns an empty list if NC is not configured or any error occurs. The
    caller is responsible for deciding what to do with an empty list.
    """
    try:
        import httpx
        from xml.etree import ElementTree
        from urllib.parse import unquote
        from app import NC_URL, NC_USER, NC_PASS, NC_BASE_PATH
        if not NC_URL or not NC_USER:
            return []
        dav_url = f"{NC_URL}/remote.php/dav/files/{NC_USER}{NC_BASE_PATH}"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request("PROPFIND", dav_url,
                                auth=httpx.BasicAuth(NC_USER, NC_PASS),
                                headers={"Depth": "1",
                                         "Content-Type": "application/xml"},
                                content=_PROPFIND_BODY)
            if r.status_code >= 300:
                return []
        ns = {"d": "DAV:"}
        root = ElementTree.fromstring(r.text)
        base = NC_BASE_PATH.strip("/").split("/")[-1]
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


async def folder_exists(name: str) -> bool:
    """Return True if a top-level folder with this name exists (case-insensitive)."""
    target = (name or "").strip().lower()
    if not target:
        return False
    folders = await list_top_folders()
    return any(f.lower() == target for f in folders)
