"""
Sharelock v2 — app configuration and extension setup.

Extension + ChatExtension init, config constants, helper functions.
All other modules import from here.
"""
import logging
import os

from imperal_sdk import Extension
from imperal_sdk.chat import ChatExtension, ActionResult

log = logging.getLogger("sharelock-v2")

# ── Extension + Chat ──────────────────────────────────────────────────────────

ext = Extension(
    "sharelock-v2",
    version="3.1.2",
    capabilities=[
        # Case CRUD + doc search
        "sharelock:cases:read", "sharelock:cases:write",
        # Analysis pipeline (run/cancel/continue/resume)
        "sharelock:analysis:run", "sharelock:analysis:cancel",
        # Forensic reports (Cases API signed URLs, gap review)
        "sharelock:reports:read", "sharelock:reports:write",
        # Nextcloud / external blob storage for evidence files
        "storage:read", "storage:write",
        # Cases metadata cache, run state
        "store:read", "store:write",
        "config:read", "config:write",
        # Federal-grade LLM pipeline (20+ phases, Opus/Haiku)
        "ai:complete",
        # Analysis progress + completion notifications
        "notify:push",
    ],
    display_name='Sharelock',
    description=(
        'Forensic case analysis — manage cases, upload documents, run AI-driven incremental analysis pipelines, generate prosecution and inspection reports, and review forensic findings.'
    ),
    icon="icon.svg",
    actions_explicit=True,
)

# Load system prompt from file
_prompt_path = os.path.join(os.path.dirname(__file__), "system_prompt.txt")
_system_prompt = ""
try:
    with open(_prompt_path) as _f:
        _system_prompt = _f.read().strip()
except Exception:
    _system_prompt = "Sharelock Intelligence module — federal investigation and forensic case analysis."

chat = ChatExtension(
    ext=ext,
    tool_name="tool_sharelock_chat",
    description="Sharelock AI forensic investigator — case analysis, evidence review, intelligence reports",
    system_prompt=_system_prompt,
)

# ── Config ────────────────────────────────────────────────────────────────────

CASES_API_URL = os.environ.get("CASES_API_URL", "http://66.78.41.10:8096")
CASES_API_KEY = os.environ.get("CASES_API_KEY", "")

# Storage backend config read from extension settings at runtime (see files.py)
# Defaults for Nextcloud when settings not yet configured
NC_URL = os.environ.get("NC_URL", "")
NC_USER = os.environ.get("NC_USER", "")
NC_PASS = os.environ.get("NC_PASSWORD", "")
NC_BASE_PATH = os.environ.get("NC_BASE_PATH", "/Sharelock/")

# ── Helpers ───────────────────────────────────────────────────────────────────


def _user_id(ctx) -> str:
    """Extract user ID from context."""
    return str(ctx.user.imperal_id) if hasattr(ctx, "user") and ctx.user else ""


def _user_email(ctx) -> str:
    """Extract user email from context."""
    return ctx.user.email if hasattr(ctx, "user") and ctx.user else "unknown"


def _user_agency(ctx) -> str:
    """Extract the user's agency_id, falling back to 'default' for legacy rows.

    Agency rollout (2026-04-18): the kernel populates ``ctx.user.agency_id``
    from the Auth GW user row. During the rollout window it may be None for
    legacy users — we return 'default' so downstream Cases API calls always
    get a concrete header. After backfill this will collapse to
    ``ctx.user.agency_id`` directly.
    """
    if not (hasattr(ctx, "user") and ctx.user):
        return "default"
    return getattr(ctx.user, "agency_id", None) or "default"


def _get_llm():
    """Get the unified LLM provider (BYOLLM, billing, per-purpose routing)."""
    from imperal_sdk.runtime.llm_provider import get_llm_provider
    return get_llm_provider()


# ── Lifecycle ─────────────────────────────────────────────────────────────────


@ext.health_check
async def health(ctx) -> dict:
    return {"status": "ok", "version": ext.version}
