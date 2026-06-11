"""Guard: NO runtime-lazy imports of ext-local modules (incident 2026-06-12).

Bare extension module names (app, queries, files, ...) resolve correctly
ONLY while the platform loader imports this extension. After load, the
loader's bare-name context belongs to whichever extension loaded last —
a function-body `import queries` re-imported against microsoft-ads'
namespace and broke every panel storage read in production.

Rule: every import of an ext-local module must be at module level
(column 0). Stdlib/third-party lazy imports are fine.
"""
import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_LOCAL_MODULES = sorted(
    f[:-3] for f in os.listdir(_ROOT)
    if f.endswith(".py") and f != "main.py"
)

_LAZY_LOCAL = re.compile(
    r"^\s+(?:import|from)\s+(" + "|".join(map(re.escape, _LOCAL_MODULES)) + r")\b"
)


def test_no_function_body_imports_of_local_modules():
    offenders = []
    for fname in os.listdir(_ROOT):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(_ROOT, fname)) as f:
            for lineno, line in enumerate(f, 1):
                if _LAZY_LOCAL.match(line):
                    offenders.append(f"{fname}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Runtime-lazy ext-local imports found (must be module-level — "
        "bare ext names resolve against the wrong extension at runtime):\n"
        + "\n".join(offenders)
    )
