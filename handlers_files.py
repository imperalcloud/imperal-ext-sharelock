"""
Sharelock v2 — Evidence upload handler (panel FileUpload → agency storage).

The panel dropzone (panels_case._build_upload_section) fires
``ui.Call("upload_case_files", case_id=...)`` immediately on file-select with
``params.files = [{name, size, mime_type, data_base64}]`` (raw base64, no
data-URI prefix). Limit violations return typed error-free SUCCESS facts
(``{uploaded: 0, reason: ...}``) — the narrator owns phrasing (ICNLI).
"""
import base64
import logging
import posixpath

from pydantic import BaseModel, Field

from app import chat, _user_agency
from auth_gate import require_unlock
from imperal_sdk.chat import ActionResult
import files
import queries
from models import UploadReceipt

log = logging.getLogger("sharelock-v2.handlers_files")

_MAX_FILE_BYTES = 10 * 1024 * 1024   # per-file decoded cap (panel max_size_mb=10)
_MAX_TOTAL_BYTES = 25 * 1024 * 1024  # batch decoded cap (panel max_total_mb=25)
_MAX_FILES = 8                       # batch count cap (panel max_files=8)
# base64 inflates payloads by 4/3 (+ padding); screening on encoded length
# rejects oversized payloads BEFORE any decode allocation.
_B64_FILE_CAP = _MAX_FILE_BYTES * 4 // 3 + 4
_B64_TOTAL_CAP = _MAX_TOTAL_BYTES * 4 // 3 + 4 * _MAX_FILES


# ── Parameter Models ──────────────────────────────────────────────────────────


class UploadFileItem(BaseModel):
    """One file from the panel FileUpload action (raw base64 payload)."""
    name: str = ""
    size: int = 0
    mime_type: str = ""
    data_base64: str = ""


class UploadCaseFilesParams(BaseModel):
    case_id: int = Field(..., description="Case ID receiving the evidence files")
    files: list[UploadFileItem] = Field(default_factory=list, description=(
        "Files from the panel uploader: name/size/mime_type/data_base64 "
        "(raw base64, no data-URI prefix)"
    ))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe_filename(name: str) -> str:
    """Basename only — a client-supplied name must not traverse out of the
    case folder (``../../etc/passwd`` → ``passwd``)."""
    return posixpath.basename((name or "").replace("\\", "/")).strip()


def _limit_fact(case_id: int, reason: str) -> ActionResult:
    """Typed error-free limit fact — a SUCCESS result, never an error."""
    return ActionResult.success(
        data={"uploaded": 0, "case_id": case_id, "files": [], "reason": reason},
        summary=f"No files uploaded: {reason}.",
    )


# ── Chat Function ─────────────────────────────────────────────────────────────


@chat.function("upload_case_files", action_type="write",
               effects=["create:file"],
               data_model=UploadReceipt,
               description=(
                   "Upload evidence files into a case's storage folder. "
                   "Files arrive base64-encoded from the panel uploader; "
                   "analysis picks them up on the next census run."
               ))
@require_unlock
async def fn_upload_case_files(ctx, params: UploadCaseFilesParams) -> ActionResult:
    """Decode + size-guard the batch, then PUT each file into the case folder
    of the agency's storage backend (files.get_agency_backend)."""
    case_id = params.case_id
    if not params.files:
        return _limit_fact(case_id, "no files in the upload payload")
    if len(params.files) > _MAX_FILES:
        return _limit_fact(
            case_id,
            f"limit {_MAX_FILES} files per upload (got {len(params.files)})")

    # Pre-decode screen on encoded lengths — no allocation before the guard.
    for f in params.files:
        if len(f.data_base64) > _B64_FILE_CAP:
            return _limit_fact(
                case_id,
                f"limit {_MAX_FILE_BYTES // (1024 * 1024)}MB per file "
                f"('{_safe_filename(f.name) or '?'}' exceeds it)")
    if sum(len(f.data_base64) for f in params.files) > _B64_TOTAL_CAP:
        return _limit_fact(
            case_id,
            f"limit {_MAX_TOTAL_BYTES // (1024 * 1024)}MB total per upload")

    agency = _user_agency(ctx)
    try:
        case = await queries.get_case(case_id, agency_id=agency)
    except Exception as e:
        return ActionResult.error(f"Could not resolve case {case_id}: {e}")
    if not case or not case.get("name"):
        return ActionResult.error(f"Case {case_id} not found.", retryable=False)
    case_name = case["name"]

    decoded: list[tuple[str, bytes]] = []
    total = 0
    for f in params.files:
        fname = _safe_filename(f.name)
        if not fname:
            return _limit_fact(case_id, "a file in the payload has no usable name")
        try:
            blob = base64.b64decode(f.data_base64 or "", validate=True)
        except Exception:
            return _limit_fact(
                case_id,
                f"'{fname}' is not valid base64 — re-select the file and retry")
        if len(blob) > _MAX_FILE_BYTES:
            return _limit_fact(
                case_id,
                f"limit {_MAX_FILE_BYTES // (1024 * 1024)}MB per file "
                f"('{fname}' exceeds it)")
        total += len(blob)
        if total > _MAX_TOTAL_BYTES:
            return _limit_fact(
                case_id,
                f"limit {_MAX_TOTAL_BYTES // (1024 * 1024)}MB total per upload")
        decoded.append((fname, blob))

    backend = await files.get_agency_backend(agency)
    uploaded: list[str] = []
    failed: list[str] = []
    for fname, blob in decoded:
        try:
            await backend.upload(case_name, blob, fname)
            uploaded.append(fname)
        except Exception as e:
            log.warning(f"upload_case_files: '{fname}' -> case {case_id} "
                        f"({case_name}) failed: {e}")
            failed.append(fname)

    if not uploaded:
        return ActionResult.error(
            f"Upload failed for all {len(failed)} file(s) — the storage "
            f"backend rejected the writes.")

    note = "analysis will pick the new files up on the next census run"
    summary = (f"Uploaded {len(uploaded)} file(s) to case **{case_name}** "
               f"(ID: {case_id}): {', '.join(uploaded)}."
               + (f" Failed: {', '.join(failed)}." if failed else "")
               + f" Note: {note}.")
    return ActionResult.success(
        data={"uploaded": len(uploaded), "case_id": case_id,
              "case_name": case_name, "files": uploaded, "failed": failed,
              "note": note},
        summary=summary,
    )
