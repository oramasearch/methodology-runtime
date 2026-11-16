# SPDX-License-Identifier: MIT

"""`methodology serve`: answer predictions from a trained model, inside the sandbox (lake#690).

The serve pod runs the methodology's own image with no credential and no network unless the person
granted it, like the run pod. Its model volume (`/work/model`, read-only) holds what
`methodology stage-model` wrote: `spec.json` and `artifact.bin`. The sandbox checks run before the
person's code is imported; the artifact is loaded once; then a small HTTP server answers Lake, the
only caller the operator's NetworkPolicy admits:

- `GET /healthz`: 200 once the model is loaded (the readiness probe);
- `POST /predict {"rows": [{...}]}`: `{"predictions": [...], "model_id": "…"}`, one per row;
- `POST /explain {"rows": [{...}]}`: `{"explanations": [...]}` when the methodology declares
  `explain`.

A refusal is `{"error": {"code", "message"}}` with 400 (the request), 413 (too large), 404, or
422 (the methodology's code raised).
"""

from __future__ import annotations

import json
import pickle
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .data import Data
from .findings import Finding, all_ok, failed
from .harness import _import_entrypoint, _json_safe
from .interface import check_module
from .manifest import DEFAULT_ENTRYPOINT, check_manifest
from .sandbox import check_credentials, check_network, default_probe

MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_ROWS = 10_000

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BUNDLE = 2
EXIT_SANDBOX = 3


class ServeError(Exception):
    def __init__(self, exit_code: int, findings: list[Finding]) -> None:
        self.exit_code = exit_code
        self.findings = findings
        super().__init__("; ".join(f"{f.check}: {f.message}" for f in findings if not f.ok) or "not servable")


class LoadedModel:
    """The methodology's module and its artifact, ready to predict."""

    def __init__(self, spec: dict[str, Any], module: Any, artifact: Any, manifest: dict) -> None:
        self.spec = spec
        self.module = module
        self.artifact = artifact
        self.manifest = manifest
        self.features: list[str] | None = spec.get("features") or None
        self.explain = manifest.get("explain") is True and callable(getattr(module, "explain", None))
        # The person's code need not be thread-safe: one call at a time.
        self._lock = threading.Lock()

    def _data(self, rows: list[dict[str, Any]]) -> Data:
        return Data(rows, None, self.features)

    def predict(self, rows: list[dict[str, Any]]) -> list[Any]:
        with self._lock:
            predictions = self.module.predict(self.artifact, self._data(rows))
        if not isinstance(predictions, (list, tuple)) and hasattr(predictions, "tolist"):
            predictions = predictions.tolist()
        if not isinstance(predictions, (list, tuple)) or len(predictions) != len(rows):
            raise ValueError(f"predict returned something other than one prediction per row ({len(rows)})")
        return [_json_safe(value) for value in predictions]

    def explain_rows(self, rows: list[dict[str, Any]]) -> list[Any]:
        with self._lock:
            explanations = self.module.explain(self.artifact, self._data(rows))
        if not isinstance(explanations, (list, tuple)) or len(explanations) != len(rows):
            raise ValueError(f"explain returned something other than one entry per row ({len(rows)})")
        return [_json_safe(value) for value in explanations]


def load(
    model_dir: Path,
    source_dir: Path,
    environment: dict[str, str],
    credential_exists: Callable[[str], bool],
    network_probe: Callable[[], list[str]] = default_probe,
) -> LoadedModel:
    """Check the sandbox, then import the code and load the artifact. Raises ServeError."""
    model_dir, source_dir = Path(model_dir), Path(source_dir)
    try:
        spec = json.loads((model_dir / "spec.json").read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise ValueError("spec.json is not a JSON object")
        body = (model_dir / "artifact.bin").read_bytes()
    except Exception as error:  # noqa: BLE001
        raise ServeError(EXIT_BUNDLE, [failed("bundle.model", f"cannot read the staged model: {error}")]) from None

    # The sandbox, before any of the person's code is imported.
    sandbox = [
        check_credentials(environment, exists=credential_exists),
        check_network(spec.get("network") is True, network_probe),
    ]
    if not all_ok(sandbox):
        raise ServeError(EXIT_SANDBOX, sandbox)

    manifest = spec.get("manifest")
    if not isinstance(manifest, dict):
        try:
            manifest = json.loads((source_dir / "methodology.json").read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001
            raise ServeError(EXIT_FAILED, [failed("manifest", f"cannot read methodology.json: {error}")]) from None
    manifest_findings = check_manifest(manifest)
    if not all_ok(manifest_findings):
        raise ServeError(EXIT_FAILED, manifest_findings)

    try:
        module = _import_entrypoint(source_dir, manifest.get("entrypoint", DEFAULT_ENTRYPOINT))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise ServeError(EXIT_FAILED, [failed("import", "importing the entrypoint failed; see the log")]) from None
    interface = check_module(module, manifest)
    if not all_ok(interface):
        raise ServeError(EXIT_FAILED, interface)

    artifact_format = spec.get("artifact_format") or "pickle"
    if artifact_format == "bytes":
        artifact: Any = body
    elif artifact_format == "pickle":
        try:
            # The artifact is the methodology's own, made by its own fit in its own sandbox; it is
            # loaded only here, in the same sandbox, never by Lake or the engine.
            artifact = pickle.loads(body)  # noqa: S301
        except Exception as error:  # noqa: BLE001
            raise ServeError(EXIT_FAILED, [failed("artifact", f"the artifact does not unpickle: {error}")]) from None
    else:
        raise ServeError(EXIT_FAILED, [failed("artifact", f"unknown artifact_format {artifact_format!r}")])
    return LoadedModel(spec, module, artifact, manifest)


def handler_for(model: LoadedModel) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "orama-methodology-serve"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # One line per request, never the rows.
            sys.stderr.write(f"{self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}\n")

        def _answer(self, status: int, body: dict) -> None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _refuse(self, status: int, code: str, message: str) -> None:
            self._answer(status, {"error": {"code": code, "message": message}})

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] == "/healthz":
                return self._answer(
                    200,
                    {
                        "ready": True,
                        "model_id": model.spec.get("model_id"),
                        "methodology_id": model.spec.get("methodology_id"),
                        "explain": model.explain,
                    },
                )
            return self._refuse(404, "not_found", "GET /healthz, POST /predict or POST /explain")

        def _rows(self) -> list[dict[str, Any]] | None:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._refuse(411, "invalid_input", "a body with Content-Length is required")
                return None
            if length > MAX_REQUEST_BYTES:
                self._refuse(413, "too_large", f"a request is at most {MAX_REQUEST_BYTES} bytes")
                return None
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._refuse(400, "invalid_input", "the body is not JSON")
                return None
            rows = body.get("rows") if isinstance(body, dict) else None
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                self._refuse(400, "invalid_input", "rows is a list of objects")
                return None
            if len(rows) > MAX_ROWS:
                self._refuse(413, "too_large", f"at most {MAX_ROWS} rows in one request")
                return None
            return rows

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path not in ("/predict", "/explain"):
                return self._refuse(404, "not_found", "POST /predict or POST /explain")
            if path == "/explain" and not model.explain:
                return self._refuse(404, "not_supported", "this methodology does not explain its predictions")
            rows = self._rows()
            if rows is None:
                return None
            try:
                if path == "/predict":
                    values = model.predict(rows)
                    return self._answer(200, {"predictions": values, "model_id": model.spec.get("model_id")})
                values = model.explain_rows(rows)
                return self._answer(200, {"explanations": values, "model_id": model.spec.get("model_id")})
            except Exception as error:  # noqa: BLE001
                traceback.print_exc()
                step = path.lstrip("/")
                return self._refuse(422, "methodology_error", f"{step} raised {type(error).__name__}: {error}")

    return Handler


def make_server(model: LoadedModel, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), handler_for(model))
    server.daemon_threads = True
    return server


def serve(
    model_dir: Path,
    source_dir: Path,
    host: str,
    port: int,
    environment: dict[str, str],
    credential_exists: Callable[[str], bool],
    network_probe: Callable[[], list[str]] = default_probe,
) -> int:
    try:
        model = load(model_dir, source_dir, environment, credential_exists, network_probe)
    except ServeError as error:
        # The operator reads this from the container's log as its termination message.
        print(json.dumps({"serving": False, "findings": [f.to_json() for f in error.findings]}), file=sys.stderr)
        return error.exit_code
    server = make_server(model, host, port)
    print(json.dumps({"serving": True, "port": server.server_address[1], "model_id": model.spec.get("model_id")}))
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


__all__ = ["LoadedModel", "ServeError", "load", "make_server", "serve"]
