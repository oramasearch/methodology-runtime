# SPDX-License-Identifier: MIT

"""Check a methodology's manifest (`methodology.json`) and its lock file (interface 1).

Every check reports a Finding; nothing raises for a bad manifest, so a person sees every missing
item at once rather than the first.
"""

from __future__ import annotations

import math
import re
from pathlib import PurePosixPath
from typing import Any

from .findings import Finding, failed, passed

INPUT_KINDS = ("tabular", "text", "time_series", "graph", "image", "audio", "document")
TARGET_KINDS = ("binary", "multiclass", "regression", "none")
PARAMETER_KINDS = ("int", "float", "categorical", "bool")
TOP_LEVEL_KEYS = {
    "interface",
    "name",
    "description",
    "entrypoint",
    "inputs",
    "parameters",
    "metrics",
    "explain",
    "packages",
    "resources",
}
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
PARAMETER_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
CPU_PATTERN = re.compile(r"^([0-9]+(\.[0-9]+)?|[0-9]+m)$")
MEMORY_PATTERN = re.compile(r"^[0-9]+(Ki|Mi|Gi|Ti|K|M|G|T)?$")
MAX_TIMEOUT_SECONDS = 86400

DEFAULT_ENTRYPOINT = "methodology.py"
DEFAULT_PACKAGES = "requirements.lock"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def relative_path_ok(value: Any, suffix: str | None = None) -> bool:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("..", "") for part in value.split("/")):
        return False
    return suffix is None or value.endswith(suffix)


def _check_inputs(inputs: Any) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(inputs, dict):
        return [failed("manifest.inputs", "inputs must be an object with kinds, target and required_features")]
    kinds = inputs.get("kinds")
    if not isinstance(kinds, list) or not kinds:
        findings.append(failed("manifest.inputs.kinds", f"inputs.kinds must list at least one of {', '.join(INPUT_KINDS)}"))
    else:
        unknown = [kind for kind in kinds if kind not in INPUT_KINDS]
        if unknown:
            findings.append(failed("manifest.inputs.kinds", f"unknown input kinds {unknown}; use {', '.join(INPUT_KINDS)}"))
        elif len(set(kinds)) != len(kinds):
            findings.append(failed("manifest.inputs.kinds", "inputs.kinds lists a kind twice"))
        else:
            findings.append(passed("manifest.inputs.kinds"))
    target = inputs.get("target")
    if not isinstance(target, dict):
        findings.append(failed("manifest.inputs.target", "inputs.target must be {required, kind}; use kind none for no target"))
    else:
        kind = target.get("kind")
        required = target.get("required")
        if kind not in TARGET_KINDS:
            findings.append(failed("manifest.inputs.target", f"inputs.target.kind must be one of {', '.join(TARGET_KINDS)}"))
        elif not isinstance(required, bool):
            findings.append(failed("manifest.inputs.target", "inputs.target.required must be true or false"))
        elif required != (kind != "none"):
            findings.append(
                failed(
                    "manifest.inputs.target",
                    "inputs.target.required must be false exactly when kind is none",
                )
            )
        else:
            findings.append(passed("manifest.inputs.target"))
    required_features = inputs.get("required_features", [])
    if not isinstance(required_features, list) or not all(
        isinstance(name, str) and name for name in required_features
    ):
        findings.append(failed("manifest.inputs.required_features", "inputs.required_features must be a list of column names"))
    unknown_keys = set(inputs) - {"kinds", "target", "required_features"}
    if unknown_keys:
        findings.append(failed("manifest.inputs", f"unknown keys in inputs: {sorted(unknown_keys)}"))
    return findings


def _check_parameter(index: int, parameter: Any) -> list[Finding]:
    where = f"manifest.parameters[{index}]"
    if not isinstance(parameter, dict):
        return [failed(where, "a parameter must be an object with name, kind and default")]
    findings: list[Finding] = []
    name = parameter.get("name")
    if not isinstance(name, str) or not PARAMETER_NAME_PATTERN.match(name):
        findings.append(failed(where, "name must match ^[a-z_][a-z0-9_]*$"))
        name = f"#{index}"
    where = f"manifest.parameters.{name}"
    kind = parameter.get("kind")
    if kind not in PARAMETER_KINDS:
        return findings + [failed(where, f"kind must be one of {', '.join(PARAMETER_KINDS)}")]
    if "default" not in parameter:
        return findings + [failed(where, "default is required")]
    default = parameter["default"]
    minimum = parameter.get("minimum")
    maximum = parameter.get("maximum")
    allowed = {"name", "kind", "default", "minimum", "maximum", "log", "choices", "description"}
    unknown = set(parameter) - allowed
    if unknown:
        findings.append(failed(where, f"unknown keys {sorted(unknown)}"))
    if kind in ("int", "float"):
        is_value = _is_int if kind == "int" else _is_number
        if not is_value(default):
            findings.append(failed(where, f"default must be {'an integer' if kind == 'int' else 'a number'}"))
        for bound_name, bound in (("minimum", minimum), ("maximum", maximum)):
            if bound is not None and not is_value(bound):
                findings.append(failed(where, f"{bound_name} must be {'an integer' if kind == 'int' else 'a number'}"))
        if _is_number(minimum) and _is_number(maximum) and minimum > maximum:
            findings.append(failed(where, "minimum is greater than maximum"))
        if _is_number(default):
            if _is_number(minimum) and default < minimum:
                findings.append(failed(where, "default is below minimum"))
            if _is_number(maximum) and default > maximum:
                findings.append(failed(where, "default is above maximum"))
        log = parameter.get("log", False)
        if not isinstance(log, bool):
            findings.append(failed(where, "log must be true or false"))
        elif log and not (_is_number(minimum) and minimum > 0):
            findings.append(failed(where, "a log-scaled parameter needs a minimum above zero"))
        if "choices" in parameter:
            findings.append(failed(where, "choices belong to categorical parameters"))
    elif kind == "categorical":
        choices = parameter.get("choices")
        if not isinstance(choices, list) or not choices:
            findings.append(failed(where, "a categorical parameter needs a non-empty choices list"))
        elif not all(isinstance(choice, (str, int, float, bool)) for choice in choices):
            findings.append(failed(where, "choices must be strings, numbers or booleans"))
        elif default not in choices:
            findings.append(failed(where, "default must be one of choices"))
    elif kind == "bool":
        if not isinstance(default, bool):
            findings.append(failed(where, "default must be true or false"))
    if not any(not finding.ok for finding in findings):
        findings.append(passed(where))
    return findings


def _check_resources(resources: Any) -> list[Finding]:
    if resources is None:
        return []
    if not isinstance(resources, dict):
        return [failed("manifest.resources", "resources must be an object")]
    findings: list[Finding] = []
    cpu = resources.get("cpu")
    if cpu is not None and not (
        (isinstance(cpu, str) and CPU_PATTERN.match(cpu) and cpu not in ("0", "0m"))
        or (_is_number(cpu) and cpu > 0)
    ):
        findings.append(failed("manifest.resources.cpu", "cpu must be a quantity such as \"1\" or \"500m\""))
    memory = resources.get("memory")
    if memory is not None and not (isinstance(memory, str) and MEMORY_PATTERN.match(memory)):
        findings.append(failed("manifest.resources.memory", "memory must be a quantity such as \"2Gi\""))
    gpu = resources.get("gpu", 0)
    if not _is_int(gpu) or gpu < 0:
        findings.append(failed("manifest.resources.gpu", "gpu must be a whole number, zero or more"))
    timeout = resources.get("timeout_seconds")
    if timeout is not None and (not _is_int(timeout) or not 0 < timeout <= MAX_TIMEOUT_SECONDS):
        findings.append(
            failed("manifest.resources.timeout_seconds", f"timeout_seconds must be 1 to {MAX_TIMEOUT_SECONDS}")
        )
    unknown = set(resources) - {"cpu", "memory", "gpu", "timeout_seconds"}
    if unknown:
        findings.append(failed("manifest.resources", f"unknown keys {sorted(unknown)}"))
    if not findings:
        findings.append(passed("manifest.resources"))
    return findings


def check_manifest(manifest: Any) -> list[Finding]:
    """Every finding about a manifest; it is usable when all are ok."""
    if not isinstance(manifest, dict):
        return [failed("manifest", "methodology.json must be a JSON object")]
    findings: list[Finding] = []
    if manifest.get("interface") != 1:
        findings.append(failed("manifest.interface", "interface must be 1"))
    else:
        findings.append(passed("manifest.interface"))
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        findings.append(failed("manifest.name", "name is required"))
    else:
        findings.append(passed("manifest.name"))
    if "description" in manifest and not isinstance(manifest["description"], str):
        findings.append(failed("manifest.description", "description must be text"))
    entrypoint = manifest.get("entrypoint", DEFAULT_ENTRYPOINT)
    if not relative_path_ok(entrypoint, ".py"):
        findings.append(failed("manifest.entrypoint", "entrypoint must be a relative path to a .py file inside the methodology"))
    else:
        findings.append(passed("manifest.entrypoint"))
    findings.extend(_check_inputs(manifest.get("inputs")))
    parameters = manifest.get("parameters", [])
    if not isinstance(parameters, list):
        findings.append(failed("manifest.parameters", "parameters must be a list"))
    else:
        names = [p.get("name") for p in parameters if isinstance(p, dict)]
        duplicates = sorted({n for n in names if isinstance(n, str) and names.count(n) > 1})
        if duplicates:
            findings.append(failed("manifest.parameters", f"parameters named twice: {duplicates}"))
        for index, parameter in enumerate(parameters):
            findings.extend(_check_parameter(index, parameter))
    metrics = manifest.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        findings.append(failed("manifest.metrics", "metrics must list at least one metric name, such as auc or rmse"))
    else:
        bad = [m for m in metrics if not isinstance(m, str) or not NAME_PATTERN.match(m)]
        if bad:
            findings.append(failed("manifest.metrics", f"metric names must match ^[a-z][a-z0-9_]*$: {bad}"))
        elif len(set(metrics)) != len(metrics):
            findings.append(failed("manifest.metrics", "a metric is listed twice"))
        elif any(m.startswith(("in_sample_", "held_out_")) for m in metrics):
            findings.append(
                failed(
                    "manifest.metrics",
                    "list base names (auc); the runner reports in_sample_auc and held_out_auc itself",
                )
            )
        else:
            findings.append(passed("manifest.metrics"))
    if "explain" in manifest and not isinstance(manifest["explain"], bool):
        findings.append(failed("manifest.explain", "explain must be true or false"))
    packages = manifest.get("packages", DEFAULT_PACKAGES)
    if not relative_path_ok(packages):
        findings.append(failed("manifest.packages", "packages must be a relative path to the lock file"))
    findings.extend(_check_resources(manifest.get("resources")))
    unknown = set(manifest) - TOP_LEVEL_KEYS
    if unknown:
        findings.append(failed("manifest", f"unknown keys {sorted(unknown)}"))
    return findings


REQUIREMENT_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9._,-]+\])?==(?P<version>[A-Za-z0-9.!+_-]+)$"
)
HASH_PATTERN = re.compile(r"^--hash=sha256:[0-9a-f]{64}$")


def _logical_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    current = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not current:
            start = number
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        current += line
        if current.strip():
            lines.append((start, current.strip()))
        current = ""
    if current.strip():
        lines.append((start, current.strip()))
    return lines


def check_lock(text: str, where: str = "requirements.lock") -> list[Finding]:
    """Every requirement pinned with ==, each with at least one sha256 hash, nothing else.

    An empty lock file is valid: the methodology uses the standard library only.
    """
    findings: list[Finding] = []
    count = 0
    for number, line in _logical_lines(text):
        if line.startswith("-"):
            option = line.split()[0]
            findings.append(
                failed(f"lock.line{number}", f"{where}:{number}: only pinned requirements with --hash are allowed, not {option}")
            )
            continue
        requirement_part, _, option_part = line.partition(" --")
        options = ("--" + option_part).split() if option_part else []
        requirement = requirement_part.split(";", 1)[0].replace(" ", "")
        if not REQUIREMENT_PATTERN.match(requirement):
            findings.append(
                failed(f"lock.line{number}", f"{where}:{number}: {requirement} is not pinned as name==version")
            )
            continue
        hashes = [option for option in options if option.startswith("--hash")]
        others = [option for option in options if not option.startswith("--hash")]
        if not hashes:
            findings.append(failed(f"lock.line{number}", f"{where}:{number}: {requirement} has no --hash=sha256:"))
            continue
        bad = [option for option in hashes if not HASH_PATTERN.match(option)]
        if bad or others:
            findings.append(
                failed(f"lock.line{number}", f"{where}:{number}: unexpected {bad + others}; use --hash=sha256:<64 hex>")
            )
            continue
        count += 1
    if not any(not finding.ok for finding in findings):
        findings.append(
            passed("lock", f"{count} pinned package{'s' if count != 1 else ''}" if count else "standard library only")
        )
    return findings
