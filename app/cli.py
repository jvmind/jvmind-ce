"""Console-script entry point shim.

When `pip install jvmind-ce` generates the `jvmind` console script, it
emits ``from app.cli import main``. The editable install's MAPPING only
covers top-level packages (``app`` and ``react_agent``), so this module
must live inside ``app/`` for the import to resolve reliably.

It also adds the project root to ``sys.path`` before importing
``server.main``, which is needed when running ``jvmind`` from a
directory that is not the package root (e.g. via the console script).
"""
from __future__ import annotations

import sys
from pathlib import Path

_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from server import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())