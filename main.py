"""
Sharelock v2 — Extension entry point.

Loaded by ICNLI OS Kernel via execute_sdk_tool.
Module purge for hot-reload, then import all submodules.
"""
import os
import sys

# ── Module purge (hot-reload support) ─────────────────────────────────────────
_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

_MODULES = (
    "app", "queries", "chat", "skeleton", "validation",
    "handlers", "handlers_analysis",
    "handlers_share", "handlers_files", "handlers_admin",
    "files", "panels", "panels_case", "panels_analysis",
    "panels_gap_review", "panels_graph",
    "panels_share", "panels_settings",
    "intelligence_context", "intelligence_format",
    "cache_models", "models_share",
)
for _m in [k for k in sys.modules if k in _MODULES]:
    del sys.modules[_m]

# ── Import core + submodules ──────────────────────────────────────────────────
from app import ext, chat  # noqa: E402, F401

# Register cache models (SDK v1.6.0 @ext.cache_model) BEFORE any submodule
# that uses ctx.cache.get_or_fetch is imported.
import cache_models  # noqa: E402, F401

import validation  # noqa: E402, F401 — pure helpers (imported by handlers)
import handlers  # noqa: E402, F401 — core chat tools
import handlers_analysis  # noqa: E402, F401 — run/cancel/gap chat tools
import handlers_share  # noqa: E402, F401 — share/unshare/list-shares chat tools
import handlers_files  # noqa: E402, F401 — upload_case_files chat tool
import handlers_admin  # noqa: E402, F401 — save_agency_settings chat tool (admin)
import skeleton  # noqa: E402, F401 — registers @ext.skeleton section
import panels  # noqa: E402, F401 — registers left panel
import panels_case  # noqa: E402, F401 — registers right panel
import panels_analysis  # noqa: E402, F401 — progress builders (used by panels_case)
import panels_gap_review  # noqa: E402, F401 — gap review builder
import panels_graph  # noqa: E402, F401 — graph builder
import panels_share  # noqa: E402, F401 — share tab builder
import panels_settings  # noqa: E402, F401 — admin settings tab builder
