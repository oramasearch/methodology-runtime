"""The interface a Lake workspace methodology implements, and the harness that runs it.

A methodology is a directory holding `methodology.json` (the manifest), an entrypoint module
(`methodology.py`) with `fit`, `predict`, `score` and optionally `explain`, and a lock file of
hash-pinned packages. See docs/interface.md.
"""

from .data import Data
from .findings import Finding

__version__ = "0.1.0"
USER_AGENT = f"orama-methodology-runtime/{__version__}"
INTERFACE_VERSION = 1

__all__ = ["Data", "Finding", "INTERFACE_VERSION", "USER_AGENT", "__version__"]
