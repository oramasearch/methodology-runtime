"""Check that a loaded entrypoint module implements the interface its manifest declares."""

from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any

from .findings import Finding, failed, passed

REQUIRED_FUNCTIONS = {
    "fit": ("data", "params"),
    "predict": ("artifact", "data"),
    "score": ("artifact", "data"),
}
OPTIONAL_FUNCTIONS = {"explain": ("artifact", "data")}


def _takes_two(function: Any) -> bool:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return True  # a builtin or C callable: trust it, the call will tell
    try:
        signature.bind(None, None)
        return True
    except TypeError:
        return False


def check_module(module: ModuleType, manifest: dict) -> list[Finding]:
    """One finding per function of the interface; all must pass before the code is called."""
    findings: list[Finding] = []
    wanted = dict(REQUIRED_FUNCTIONS)
    if manifest.get("explain") is True:
        wanted.update(OPTIONAL_FUNCTIONS)
    for name, arguments in wanted.items():
        function = getattr(module, name, None)
        check = f"interface.{name}"
        if function is None:
            findings.append(failed(check, f"{name}({', '.join(arguments)}) is missing"))
        elif not callable(function):
            findings.append(failed(check, f"{name} is not a function"))
        elif not _takes_two(function):
            findings.append(failed(check, f"{name} must take ({', '.join(arguments)})"))
        else:
            findings.append(passed(check))
    return findings
