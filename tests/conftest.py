"""Make the sharelock-v2 extension package importable for tests.

We run pytest from /opt/extensions/sharelock-v2, so the extension modules
(chat, intelligence_validator, ...) are importable as top-level names by
prepending the parent directory to sys.path.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
