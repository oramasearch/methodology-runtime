# SPDX-License-Identifier: MIT

"""`methodology stage` and `methodology collect`: the only way data enters and leaves a run.

Both run in the pristine runtime image, never in the methodology's own image, with the run's
credential (`lmr.<run id>.<secret>`) read from a file. They call two Lake capabilities over REST:

- `POST /v1/methodology_run_input {run_id, offset, limit}`, paged until `next_offset` is null;
- `POST /v1/methodology_run_artifact {run_id, offset, chunk_base64}`, in order, for an artifact
  larger than `INLINE_ARTIFACT_BYTES` (lake#690);
- `POST /v1/methodology_run_output {run_id, result, artifact_base64? | artifact?, log?}`, once.

A model deployment's stage (`methodology stage-model`, lake#690) calls one more, with the
deployment's credential (`lmd.<deployment id>.<secret>`):

- `POST /v1/methodology_deployment_input {deployment_id, offset, limit}`, paged until
  `next_offset` is null: the deployment's spec and its model's artifact.

The credential is sent only as the bearer and is never printed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO, Callable

from . import USER_AGENT

PAGE_LIMIT = 5000
# An artifact up to this size is sent inline with the output, base64 in one body; a larger one is
# sent first in chunks of ARTIFACT_CHUNK_BYTES, up to MAX_ARTIFACT_BYTES (lake#690).
INLINE_ARTIFACT_BYTES = 24 * 1024 * 1024
ARTIFACT_CHUNK_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
LOG_TAIL_BYTES = 64 * 1024
RETRIES = 5
REQUEST_TIMEOUT_SECONDS = 120

EXIT_OK = 0
EXIT_TRANSFER = 4


class TransferError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def read_credential(path: Path) -> str:
    credential = Path(path).read_text(encoding="utf-8").strip()
    if not credential:
        raise TransferError(f"the credential file {path} is empty")
    return credential


class LakeClient:
    """A small JSON client for Lake's REST capabilities, with retries for transient failures."""

    def __init__(
        self,
        base_url: str,
        credential: str,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = RETRIES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._credential = credential
        self._sleep = sleep
        self._retries = retries

    def call(self, capability: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        url = f"{self.base_url}/v1/{capability}"
        attempt = 0
        while True:
            attempt += 1
            request = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._credential}",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    return self._unwrap(response.read())
            except urllib.error.HTTPError as error:
                payload = error.read()
                code, message = self._error(payload)
                transient = error.code in (429, 500, 502, 503, 504)
                if transient and attempt <= self._retries:
                    self._sleep(self._delay(attempt, error.headers.get("Retry-After")))
                    continue
                raise TransferError(
                    f"{capability} answered {error.code} {code or ''}: {message}".strip(),
                    status=error.code,
                    code=code,
                ) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                if attempt <= self._retries:
                    self._sleep(self._delay(attempt, None))
                    continue
                raise TransferError(f"{capability} could not reach Lake: {error}") from None

    @staticmethod
    def _delay(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return float(min(2 ** (attempt - 1), 30))

    @staticmethod
    def _error(payload: bytes) -> tuple[str | None, str]:
        try:
            body = json.loads(payload)
            error = body.get("error", {}) if isinstance(body, dict) else {}
            return error.get("code"), error.get("message", "")
        except (ValueError, AttributeError):
            return None, payload[:200].decode("utf-8", "replace")

    @staticmethod
    def _unwrap(payload: bytes) -> dict[str, Any]:
        body = json.loads(payload)
        if isinstance(body, dict) and "ok" in body:
            if not body["ok"]:
                error = body.get("error", {})
                raise TransferError(error.get("message", "Lake refused the call"), code=error.get("code"))
            body = body.get("value", {})
        if not isinstance(body, dict):
            raise TransferError("Lake answered something other than a JSON object")
        return body


def stage(client: LakeClient, run_id: str, out_dir: Path, limit: int = PAGE_LIMIT) -> dict[str, Any]:
    """Page the run's input into out_dir/spec.json and out_dir/data.jsonl; return a summary."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_dir / "data.jsonl.partial"
    offset = 0
    rows_written = 0
    spec: dict[str, Any] | None = None
    total_rows = None
    with open(partial, "w", encoding="utf-8") as data:
        while True:
            page = client.call("methodology_run_input", {"run_id": run_id, "offset": offset, "limit": limit})
            if spec is None:
                spec = page.get("spec")
                if not isinstance(spec, dict):
                    raise TransferError("methodology_run_input answered no spec")
                total_rows = page.get("total_rows")
            rows = page.get("rows", [])
            if not isinstance(rows, list):
                raise TransferError("methodology_run_input answered rows that are not a list")
            for row in rows:
                if not isinstance(row, dict):
                    raise TransferError("methodology_run_input answered a row that is not an object")
                data.write(json.dumps(row, separators=(",", ":")) + "\n")
            rows_written += len(rows)
            next_offset = page.get("next_offset")
            if next_offset is None:
                break
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise TransferError(f"methodology_run_input answered next_offset {next_offset!r} after {offset}")
            offset = next_offset
    if isinstance(total_rows, int) and total_rows != rows_written:
        raise TransferError(f"Lake said {total_rows} rows and sent {rows_written}")
    partial.replace(out_dir / "data.jsonl")
    (out_dir / "spec.json").write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    return {"rows": rows_written, "mode": spec.get("mode")}


def missing_result(exit_code: int | None) -> dict[str, Any]:
    """What collect reports when the run pod left no result.json."""
    reason = "the run produced no result: it was stopped, ran out of memory or time, or exited before writing one"
    if exit_code is not None:
        reason += f" (exit code {exit_code})"
    return {
        "interface": 1,
        "status": "failed",
        "findings": [{"check": "run.result", "ok": False, "message": reason}],
        "metrics": {},
        "rows": {"train": 0, "held_out": 0},
        "predictions_sample": [],
        "explain_supported": False,
        "artifact_format": None,
        "error": "no result",
    }


class RunOutputs:
    """The run's output directory, opened once without following symbolic links (methodology-runtime#5).

    Everything in it was written by code that is not trusted, so collect reads it only as data:
    never imported, and only regular files named by the contract. The directory itself and each file
    are opened with O_NOFOLLOW (each file relative to the opened directory), and a file is used only
    when fstat on the opened descriptor says it is a regular file. A symbolic link (to the run's
    credential, say), a named pipe or a device is refused with a `run.output_file` finding.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.refusals: list[dict[str, Any]] = []
        self.refused: set[str] = set()
        self._fd: int | None = None
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            self._fd = os.open(self.directory, flags)
        except FileNotFoundError:
            pass
        except OSError:
            self._refuse(f"the output directory {self.directory} is a symbolic link or not a directory; "
                         "collect read nothing from it")

    def _refuse(self, message: str) -> None:
        self.refusals.append({"check": "run.output_file", "ok": False, "message": message})

    def open(self, name: str) -> BinaryIO | None:
        """`name` opened for reading when it is a regular file in the directory; None when it is
        absent or refused (the refusal is recorded)."""
        if self._fd is None:
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(name, flags, dir_fd=self._fd)
        except FileNotFoundError:
            return None
        except OSError:
            self.refused.add(name)
            self._refuse(f"{name} in the run's output is a symbolic link or cannot be opened; collect did not read it")
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                self.refused.add(name)
                self._refuse(f"{name} in the run's output is not a regular file; collect did not read it")
                os.close(fd)
                return None
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "RunOutputs":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _add_findings(result: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    existing = result.get("findings")
    if not isinstance(existing, list):
        existing = []
    result["findings"] = existing + findings


def _output_body(
    run_id: str, outputs: RunOutputs, exit_code: int | None
) -> tuple[dict[str, Any], BinaryIO | None]:
    """The body collect posts, and the artifact's open file when it is to be sent in chunks."""
    result: Any = None
    handle = outputs.open("result.json")
    if handle is not None:
        with handle:
            try:
                result = json.loads(handle.read().decode("utf-8"))
            except ValueError:
                result = None
    if not isinstance(result, dict):
        result = missing_result(exit_code)
    if exit_code is not None:
        result["exit_code"] = exit_code
    body: dict[str, Any] = {"run_id": run_id, "result": result}
    chunked: BinaryIO | None = None
    if result.get("status") == "ok":
        artifact = outputs.open("artifact.bin")
        if artifact is None and "artifact.bin" in outputs.refused:
            result["status"] = "failed"
            result["error"] = "artifact refused"
        elif artifact is not None:
            size = os.fstat(artifact.fileno()).st_size
            if size > MAX_ARTIFACT_BYTES:
                artifact.close()
                result["status"] = "failed"
                _add_findings(result, [{
                    "check": "run.artifact_size",
                    "ok": False,
                    "message": f"the artifact is {size} bytes; Lake accepts at most {MAX_ARTIFACT_BYTES}",
                }])
                result["error"] = "artifact too large"
            elif size > INLINE_ARTIFACT_BYTES:
                # Sent in chunks before the output (collect does it); the output names what was sent.
                body["artifact"] = {"bytes": size, "sha256": file_sha256(artifact)}
                chunked = artifact
            else:
                with artifact:
                    body["artifact_base64"] = base64.b64encode(artifact.read()).decode("ascii")
    log = outputs.open("log.txt")
    if log is not None:
        with log:
            size = os.fstat(log.fileno()).st_size
            log.seek(max(0, size - LOG_TAIL_BYTES))
            body["log"] = log.read(LOG_TAIL_BYTES).decode("utf-8", "replace")
    if outputs.refusals:
        _add_findings(result, list(outputs.refusals))
    return body, chunked


def output_body(run_id: str, in_dir: Path, exit_code: int | None = None) -> dict[str, Any]:
    """The body collect posts: the result, the artifact when there is one, the log's tail."""
    with RunOutputs(in_dir) as outputs:
        body, chunked = _output_body(run_id, outputs, exit_code)
        if chunked is not None:
            chunked.close()
        return body


def file_sha256(source: Path | BinaryIO) -> str:
    digest = hashlib.sha256()
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as handle:
            return file_sha256(handle)
    source.seek(0)
    for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def send_artifact(client: LakeClient, run_id: str, source: Path | BinaryIO, chunk_bytes: int | None = None) -> int:
    """Send a large artifact in order, `chunk_bytes` at a time. Lake answers how many bytes it holds
    (`received`); a resent chunk replaces what it held from that offset, so a collect that is run
    again starts over safely. Returns the bytes sent. `source` is the file collect already opened
    and checked (or a path, for a caller that has none)."""
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as handle:
            return send_artifact(client, run_id, handle, chunk_bytes)
    chunk_bytes = chunk_bytes or ARTIFACT_CHUNK_BYTES
    size = os.fstat(source.fileno()).st_size
    offset = 0
    while offset < size:
        source.seek(offset)
        chunk = source.read(chunk_bytes)
        answer = client.call(
            "methodology_run_artifact",
            {"run_id": run_id, "offset": offset, "chunk_base64": base64.b64encode(chunk).decode("ascii")},
        )
        received = answer.get("received")
        if not isinstance(received, int) or received != offset + len(chunk):
            raise TransferError(
                f"methodology_run_artifact answered received {received!r} after a chunk at {offset} "
                f"of {len(chunk)} bytes"
            )
        offset = received
    return offset


def collect(client: LakeClient, run_id: str, in_dir: Path, exit_code: int | None = None) -> dict[str, Any]:
    """Post the run's output. The run's files are read only as data, through RunOutputs; nothing in
    the output directory is ever imported or executed."""
    with RunOutputs(in_dir) as outputs:
        body, chunked = _output_body(run_id, outputs, exit_code)
        if chunked is not None:
            with chunked:
                try:
                    send_artifact(client, run_id, chunked)
                except TransferError as error:
                    # Already recorded by an earlier collect: the output call below says so.
                    if not (error.code == "conflict" and "already" in str(error)):
                        raise
    try:
        return client.call("methodology_run_output", body)
    except TransferError as error:
        # A retry after a lost answer finds the output already recorded: that is success.
        if error.code == "conflict" and "already" in str(error):
            return {"recorded": True, "model_id": None, "already_recorded": True}
        raise


def stage_model(client: LakeClient, deployment_id: str, out_dir: Path, limit: int = ARTIFACT_CHUNK_BYTES) -> dict[str, Any]:
    """Page a model deployment's spec and artifact into out_dir/spec.json and out_dir/artifact.bin
    (lake#690). The artifact is checked against the size and SHA-256 Lake names before it is
    renamed into place, so the serve pod never loads a partial one."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_dir / "artifact.bin.partial"
    digest = hashlib.sha256()
    offset = 0
    spec: dict[str, Any] | None = None
    total = None
    expected_sha = None
    with open(partial, "wb") as artifact:
        while True:
            page = client.call(
                "methodology_deployment_input", {"deployment_id": deployment_id, "offset": offset, "limit": limit}
            )
            if spec is None:
                spec = page.get("spec")
                if not isinstance(spec, dict):
                    raise TransferError("methodology_deployment_input answered no spec")
                total = page.get("total_bytes")
                expected_sha = page.get("sha256")
            encoded = page.get("chunk_base64", "")
            if not isinstance(encoded, str):
                raise TransferError("methodology_deployment_input answered a chunk that is not base64 text")
            try:
                chunk = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise TransferError("methodology_deployment_input answered a chunk that is not base64") from None
            artifact.write(chunk)
            digest.update(chunk)
            offset += len(chunk)
            next_offset = page.get("next_offset")
            if next_offset is None:
                break
            if not isinstance(next_offset, int) or next_offset != offset:
                raise TransferError(f"methodology_deployment_input answered next_offset {next_offset!r} at {offset}")
    if isinstance(total, int) and total != offset:
        partial.unlink(missing_ok=True)
        raise TransferError(f"Lake said the artifact is {total} bytes and sent {offset}")
    if isinstance(expected_sha, str) and expected_sha and expected_sha != digest.hexdigest():
        partial.unlink(missing_ok=True)
        raise TransferError("the artifact Lake sent does not match its SHA-256")
    partial.replace(out_dir / "artifact.bin")
    (out_dir / "spec.json").write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    return {"bytes": offset, "model_id": spec.get("model_id")}
