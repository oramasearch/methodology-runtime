# SPDX-License-Identifier: MIT

"""The interface a Lake workspace methodology implements, and the harness that runs it.

A methodology is a directory holding `methodology.json` (the manifest), an entrypoint module
(`methodology.py`) with `fit`, `predict`, `score` and optionally `explain`, and a lock file of
hash-pinned packages. See docs/interface.md.
"""

from __future__ import annotations

import os as _os
import sys as _sys

# The per-run volume the untrusted run pod writes (methodology-runtime#4). Nothing under it, and
# not the working directory, may be on sys.path when stage or collect import anything.
SANDBOX_WRITABLE_PATHS = ("/work",)


def _without_sandbox_writable(path: list[str], cwd: str, package_root: str) -> list[str]:
    """`path` without the working directory ('' or its absolute form) and anything under
    SANDBOX_WRITABLE_PATHS, keeping the directory this package was loaded from."""
    root = _os.path.realpath(package_root)
    here = _os.path.realpath(cwd)
    kept = []
    for entry in path:
        resolved = _os.path.realpath(entry or here)
        writable = resolved == here or any(
            resolved == top or resolved.startswith(top + _os.sep) for top in SANDBOX_WRITABLE_PATHS
        )
        if writable and resolved != root:
            continue
        kept.append(entry)
    return kept


def _drop_sandbox_writable_paths() -> None:
    # The launcher (bin/methodology) starts Python with -P, so the working directory is never on
    # the path. This is the second layer for any other launcher: it runs before this package
    # imports anything else. It cannot undo a planted `orama_methodology` shadowing this package
    # itself; only -P (or PYTHONSAFEPATH=1) prevents that.
    try:
        cwd = _os.getcwd()
    except OSError:
        cwd = _os.sep
    package_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    _sys.path[:] = _without_sandbox_writable(_sys.path, cwd, package_root)


_drop_sandbox_writable_paths()

from .data import Data  # noqa: E402  (after the path is cleaned)
from .findings import Finding  # noqa: E402

__version__ = "0.1.0"
USER_AGENT = f"orama-methodology-runtime/{__version__}"
INTERFACE_VERSION = 1

__all__ = ["Data", "Finding", "INTERFACE_VERSION", "SANDBOX_WRITABLE_PATHS", "USER_AGENT", "__version__"]
