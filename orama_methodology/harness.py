"""`methodology run`: run a methodology on the data bundle, inside the sandbox.

Reads /work/in (spec.json, data.jsonl), writes /work/out (result.json, artifact.bin, log.txt).
The sandbox checks run first; the person's code is imported only once they pass.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import math
import os
import pickle
import random
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from . import INTERFACE_VERSION
from .data import Data
from .findings import Finding, all_ok, failed, passed
from .interface import check_module
from .manifest import DEFAULT_ENTRYPOINT, DEFAULT_PACKAGES, check_lock, check_manifest
from .sandbox import check_credentials, check_network, default_probe

LOG_TAIL_BYTES = 64 * 1024
PREDICTIONS_SAMPLE = 20
EXPLAIN_SAMPLE = 20

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BUNDLE = 2
EXIT_SANDBOX = 3


class RunOptions:
    def __init__(
        self,
        in_dir: Path,
        out_dir: Path,
        source_dir: Path,
        environment: dict[str, str] | None = None,
        credential_exists: Callable[[str], bool] = os.path.exists,
        network_probe: Callable[[], list[str]] = default_probe,
        capture_output: bool = True,
    ) -> None:
        self.in_dir = Path(in_dir)
        self.out_dir = Path(out_dir)
        self.source_dir = Path(source_dir)
        self.environment = dict(os.environ) if environment is None else environment
        self.credential_exists = credential_exists
        self.network_probe = network_probe
        self.capture_output = capture_output


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    for cast in (float, str):
        try:
            return _json_safe(cast(value))
        except Exception:  # noqa: BLE001
            continue
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@contextlib.contextmanager
def _captured(log_path: Path, enabled: bool):
    """Send this process's stdout and stderr (file descriptors 1 and 2) to `log_path`."""
    if not enabled:
        yield
        return
    sys.stdout.flush()
    sys.stderr.flush()
    saved = (os.dup(1), os.dup(2))
    with open(log_path, "ab") as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        try:
            yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])


def _write_log_tail(full: Path, out: Path) -> None:
    data = full.read_bytes() if full.exists() else b""
    if len(data) > LOG_TAIL_BYTES:
        data = data[-LOG_TAIL_BYTES:]
    (out / "log.txt").write_bytes(data)
    with contextlib.suppress(FileNotFoundError):
        full.unlink()


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"data.jsonl line {number} is not a JSON object")
            rows.append(row)
    return rows


def split_indices(count: int, fraction: float, seed: int) -> tuple[list[int], list[int]]:
    """A seeded split: (train, held_out). Held out is at least one row when there are two or more."""
    indices = list(range(count))
    random.Random(seed).shuffle(indices)
    held = int(round(count * fraction))
    if fraction > 0 and count >= 2:
        held = min(max(held, 1), count - 1)
    else:
        held = 0
    return sorted(indices[held:]), sorted(indices[:held])


def resolve_params(manifest: dict, given: Any) -> tuple[dict[str, Any], list[Finding]]:
    """The manifest's defaults overlaid with the run's parameters, each checked."""
    declared = {p["name"]: p for p in manifest.get("parameters", []) if isinstance(p, dict) and "name" in p}
    params = {name: p.get("default") for name, p in declared.items()}
    findings: list[Finding] = []
    if given is None:
        given = {}
    if not isinstance(given, dict):
        return params, [failed("params", "params must be an object")]
    for name, value in given.items():
        parameter = declared.get(name)
        if parameter is None:
            findings.append(failed(f"params.{name}", f"{name} is not a parameter of this methodology"))
            continue
        kind = parameter.get("kind")
        ok = True
        if kind == "int":
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif kind == "float":
            ok = _number(value) is not None and not isinstance(value, bool)
        elif kind == "bool":
            ok = isinstance(value, bool)
        elif kind == "categorical":
            ok = value in parameter.get("choices", [])
        if ok and kind in ("int", "float"):
            minimum, maximum = parameter.get("minimum"), parameter.get("maximum")
            ok = (minimum is None or value >= minimum) and (maximum is None or value <= maximum)
        if not ok:
            findings.append(failed(f"params.{name}", f"{name}={value!r} is not a valid {kind} for this parameter"))
            continue
        params[name] = value
    return params, findings


def _check_scores(scores: Any, metrics: list[str], prefix: str) -> tuple[dict[str, float], list[Finding]]:
    check = f"score.{prefix.rstrip('_')}"
    if not isinstance(scores, dict):
        return {}, [failed(check, "score must return a dict of metric name to number")]
    values: dict[str, float] = {}
    missing = []
    not_numbers = []
    for metric in metrics:
        if metric not in scores:
            missing.append(metric)
            continue
        number = _number(scores[metric])
        if number is None:
            not_numbers.append(metric)
        else:
            values[prefix + metric] = number
    problems = []
    if missing:
        problems.append(f"score did not return {', '.join(missing)}")
    if not_numbers:
        problems.append(f"score returned values that are not finite numbers for {', '.join(not_numbers)}")
    if problems:
        return values, [failed(check, "; ".join(problems))]
    return values, [passed(check)]


def _import_entrypoint(source_dir: Path, entrypoint: str):
    path = source_dir / entrypoint
    for extra in (source_dir / ".packages", source_dir):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    spec = importlib.util.spec_from_file_location("methodology_entrypoint", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {entrypoint}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serialize(artifact: Any) -> tuple[bytes, str]:
    if isinstance(artifact, (bytes, bytearray, memoryview)):
        return bytes(artifact), "bytes"
    return pickle.dumps(artifact, protocol=pickle.HIGHEST_PROTOCOL), "pickle"


def _write_result(out_dir: Path, result: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    temporary = out_dir / "result.json.tmp"
    temporary.write_text(json.dumps(_json_safe(result), indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(out_dir / "result.json")


def run(options: RunOptions) -> int:
    """Run the bundle; always writes result.json and log.txt; returns the process exit code."""
    started = time.monotonic()
    out_dir = options.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    full_log = Path(tempfile.mkstemp(prefix="methodology-log-", dir=str(out_dir))[1])
    findings: list[Finding] = []
    result: dict[str, Any] = {
        "interface": INTERFACE_VERSION,
        "status": "failed",
        "findings": [],
        "metrics": {},
        "rows": {"train": 0, "held_out": 0},
        "predictions_sample": [],
        "explain_supported": False,
        "artifact_format": None,
        "duration_seconds": 0.0,
    }
    exit_code = EXIT_FAILED

    def finish(code: int, error: str | None = None) -> int:
        result["findings"] = [finding.to_json() for finding in findings]
        result["status"] = "ok" if code == EXIT_OK else "failed"
        if error:
            result["error"] = error
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        _write_result(out_dir, result)
        _write_log_tail(full_log, out_dir)
        return code

    # 1. The bundle.
    try:
        spec = json.loads((options.in_dir / "spec.json").read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise ValueError("spec.json is not a JSON object")
    except Exception as error:  # noqa: BLE001
        findings.append(failed("bundle.spec", f"cannot read spec.json: {error}"))
        return finish(EXIT_BUNDLE, "the data bundle is incomplete")
    result["run_id"] = spec.get("run_id")
    result["mode"] = spec.get("mode")
    mode = spec.get("mode")
    if mode not in ("validate", "train"):
        findings.append(failed("bundle.mode", "mode must be validate or train"))
        return finish(EXIT_BUNDLE, "the data bundle is incomplete")

    # 2. The sandbox, before any of the person's code is imported.
    sandbox = [
        check_credentials(options.environment, exists=options.credential_exists),
        check_network(spec.get("network") is True, options.network_probe),
    ]
    findings.extend(sandbox)
    if not all_ok(sandbox):
        return finish(EXIT_SANDBOX, "the sandbox is not what this run needs; the code was not run")

    # 3. The manifest and lock file.
    manifest = spec.get("manifest")
    if not isinstance(manifest, dict):
        manifest_path = options.source_dir / "methodology.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001
            findings.append(failed("manifest", f"cannot read methodology.json: {error}"))
            return finish(EXIT_FAILED, "the methodology has no readable manifest")
    manifest_findings = check_manifest(manifest)
    findings.extend(manifest_findings)
    if not all_ok(manifest_findings):
        return finish(EXIT_FAILED, "the manifest needs changes")
    packages = manifest.get("packages", DEFAULT_PACKAGES)
    lock_path = options.source_dir / packages if isinstance(packages, str) else None
    if lock_path is not None and lock_path.is_file():
        findings.extend(check_lock(lock_path.read_text(encoding="utf-8"), packages))
    elif "packages" in manifest:
        findings.append(failed("lock", f"the lock file {packages} is missing"))
    else:
        findings.append(passed("lock", "no lock file: standard library only"))
    params, param_findings = resolve_params(manifest, spec.get("params"))
    findings.extend(param_findings)
    # A lock file or parameter problem does not stop the interface check: Validate reports every
    # missing item at once. Nothing is fitted until all of them pass.

    # 4. The data.
    try:
        rows = _load_rows(options.in_dir / "data.jsonl")
    except Exception as error:  # noqa: BLE001
        findings.append(failed("bundle.data", f"cannot read data.jsonl: {error}"))
        return finish(EXIT_BUNDLE, "the data bundle is incomplete")
    target = spec.get("target")
    target_spec = manifest["inputs"]["target"]
    data_findings: list[Finding] = []
    if target_spec["required"] and not target:
        data_findings.append(failed("data.target", "this methodology needs a target column and the run names none"))
    data = Data(rows, target if target_spec["required"] or target else None, spec.get("features") or None)
    if data.target and data.target not in data.columns:
        data_findings.append(failed("data.target", f"the target column {data.target} is not in the data"))
    missing_features = [name for name in manifest["inputs"].get("required_features", []) if name not in data.columns]
    if missing_features:
        data_findings.append(
            failed("data.features", f"required features missing from the data: {', '.join(missing_features)}")
        )
    if len(data) < 2:
        data_findings.append(failed("data.rows", "at least two rows are needed to fit and hold some out"))
    if not data_findings:
        data_findings.append(passed("data", f"{len(data)} rows, {len(data.features)} features"))
    findings.extend(data_findings)
    ready = all_ok(findings)

    fraction = spec.get("holdout_fraction", 0.2)
    if _number(fraction) is None or not 0 < float(fraction) < 1:
        fraction = 0.2
    seed = spec.get("seed", 42)
    if not isinstance(seed, int) or isinstance(seed, bool):
        seed = 42
    train_index, held_index = split_indices(len(data), float(fraction), seed)
    train, held_out = data.subset(train_index), data.subset(held_index)
    result["rows"] = {"train": len(train), "held_out": len(held_out)}

    # 5. The person's code, with its output captured.
    metrics: list[str] = manifest["metrics"]
    with _captured(full_log, options.capture_output):
        try:
            module = _import_entrypoint(options.source_dir, manifest.get("entrypoint", DEFAULT_ENTRYPOINT))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            findings.append(failed("import", "importing the entrypoint failed; see the log"))
            module = None
        if module is not None:
            interface = check_module(module, manifest)
            findings.extend(interface)
            if all_ok(interface) and ready:
                exit_code = _exercise(module, manifest, mode, params, train, held_out, metrics, findings, result, out_dir)
    if module is None or not all_ok(findings):
        return finish(EXIT_FAILED, result.get("error") or "the methodology is not usable yet; see findings")
    return finish(exit_code)


def _exercise(module, manifest, mode, params, train, held_out, metrics, findings, result, out_dir) -> int:
    step = "fit"
    try:
        artifact = module.fit(train, dict(params))
        findings.append(passed("run.fit"))
        step = "predict"
        predictions = module.predict(artifact, held_out)
        if not isinstance(predictions, (list, tuple)) and hasattr(predictions, "tolist"):
            predictions = predictions.tolist()
        if not isinstance(predictions, (list, tuple)) or len(predictions) != len(held_out):
            findings.append(
                failed("run.predict", f"predict must return one prediction per row ({len(held_out)})")
            )
        else:
            findings.append(passed("run.predict"))
            result["predictions_sample"] = list(predictions[:PREDICTIONS_SAMPLE])
        step = "score"
        in_sample, in_findings = _check_scores(module.score(artifact, train), metrics, "in_sample_")
        held, held_findings = _check_scores(module.score(artifact, held_out), metrics, "held_out_")
        findings.extend(in_findings + held_findings)
        result["metrics"] = {**in_sample, **held}
        if manifest.get("explain") is True:
            step = "explain"
            sample = held_out.subset(range(min(EXPLAIN_SAMPLE, len(held_out))))
            explanations = module.explain(artifact, sample)
            if (
                isinstance(explanations, (list, tuple))
                and len(explanations) == len(sample)
                and all(
                    isinstance(entry, dict) and all(_number(v) is not None for v in entry.values())
                    for entry in explanations
                )
            ):
                findings.append(passed("run.explain"))
                result["explain_supported"] = True
            else:
                findings.append(failed("run.explain", "explain must return one dict of feature to number per row"))
        step = "artifact"
        body, artifact_format = _serialize(artifact)
        result["artifact_format"] = artifact_format
        result["artifact_bytes"] = len(body)
        if mode == "train":
            (out_dir / "artifact.bin").write_bytes(body)
        findings.append(passed("run.artifact", f"{len(body)} bytes ({artifact_format})"))
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        findings.append(failed(f"run.{step}", f"{step} raised {type(error).__name__}: {error}"))
        result["error"] = f"{step} raised {type(error).__name__}"
        return EXIT_FAILED
    return EXIT_OK if all_ok(findings) else EXIT_FAILED
