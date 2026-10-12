# The `methodology` command

In the image: `/opt/orama/bin/methodology` (the entrypoint) and `/opt/orama/bin/install-locked`;
the package is `/opt/orama/lib/orama_methodology`. The command sets `PYTHONPATH=/opt/orama/lib`
itself and runs `python3 -s`, so neither an inherited `PYTHONPATH` nor user site-packages are used.
`run` adds `/methodology` and `/methodology/.packages` to `sys.path` only for the methodology's
import. The image runs as uid 10001, working directory `/work`.

| Command | Where | What |
|---|---|---|
| `methodology stage --lake URL --run ID --credential-file /var/run/orama/credential --out /work/in [--limit 5000]` | stage pod | pages the run's input from Lake |
| `methodology run --in /work/in --out /work/out --source /methodology` | run pod | checks the sandbox, runs the methodology |
| `methodology collect --lake URL --run ID --credential-file /var/run/orama/credential --in /work/out [--run-exit-code N]` | collect pod | posts the result once (a large artifact in chunks first) |
| `methodology stage-model --lake URL --deployment ID --credential-file /var/run/orama/credential --out /work/model` | a model deployment's stage pod | pages the model's artifact from Lake (lake#690) |
| `methodology serve --model /work/model --source /methodology --port 8080` | a model deployment's serve pod | checks the sandbox, loads the model, answers predictions (lake#690) |
| `methodology validate-manifest [DIR]` | anywhere | checks a methodology directory's manifest, lock file and entrypoint, without data |
| `install-locked [DIR] [LOCK]` (= `methodology install-locked`) | image build | installs the lock file into `DIR/.packages` |

`--lake`, `--run` and `--deployment` default to `LAKE_URL`, `RUN_ID` and `DEPLOYMENT_ID` from the environment (set only on the stage
and collect pods; the run pod would refuse `LAKE_URL`). `--lake` is Lake's in-cluster URL, the one
`LAKE_INTERNAL_URL` names.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done (`run`: `status` ok) |
| 1 | `run`: the methodology failed a check or raised; `validate-manifest`/`install-locked`: invalid |
| 2 | `run`: the bundle is incomplete (no readable `spec.json` or `data.jsonl`, unknown mode); usage error |
| 3 | `run`: the sandbox check refused the run; the code was not imported |
| 4 | `stage`/`collect`/`stage-model`: Lake refused or could not be reached after retries |

`serve` exits only when it cannot serve: 2 (no staged model), 3 (the sandbox check refused; the code
was not imported) or 1 (the manifest, the import, the interface or the artifact failed). It prints
the findings as one JSON line on stderr, which the operator reads as the container's termination
message.

`run` always writes `result.json` and `log.txt`, whatever its exit code.

## The HTTP calls

Both are `POST` with `Content-Type: application/json`, `Authorization: Bearer <credential>`, and
`User-Agent: orama-methodology-runtime/<version>`. A Lake answer `{"ok": true, "value": {...}}` is
unwrapped; `{"ok": false, "error": {code, message}}` is a refusal.

**Retries.** 429, 500, 502, 503 and 504 and connection failures are retried five times, waiting
`Retry-After` (at most 60 s) or 1, 2, 4, 8, 16 s. Any other status fails at once (exit 4); the
credential never appears in a message.

### `methodology_run_input` (stage)

Request, repeated with the `next_offset` of each answer until it is `null`:

```json
{"run_id": "<uuid>", "offset": 0, "limit": 5000}
```

Answer (`value`):

```json
{"spec": {"interface": 1, "run_id": "…", "mode": "validate|train", "methodology_id": "W-…",
          "params": {}, "target": "churned", "features": ["tenure"], "holdout_fraction": 0.2,
          "seed": 42, "network": false, "manifest": {}},
 "rows": [{"tenure": 3, "churned": 1}],
 "next_offset": 5000,
 "total_rows": 12000}
```

`spec` is read from the first page. Rows are written to `data.jsonl.partial` and renamed to
`data.jsonl` only after the last page; a `total_rows` that differs from the rows received, a
`next_offset` that does not move forward, or a row that is not an object fails the stage.

### `methodology_run_output` (collect)

Sent once:

```json
{"run_id": "<uuid>",
 "result": {"interface": 1, "status": "ok|failed", "findings": [{"check": "…", "ok": true, "message": "…"}],
            "metrics": {"in_sample_auc": 0.91, "held_out_auc": 0.84},
            "rows": {"train": 800, "held_out": 200}, "predictions_sample": [],
            "explain_supported": false, "artifact_format": "pickle|bytes|null",
            "artifact_bytes": 1234, "duration_seconds": 1.2, "run_id": "…", "mode": "train",
            "error": "…", "exit_code": 0},
 "artifact_base64": "…",
 "log": "…last 64 KiB of the code's output…"}
```

- The artifact is sent only when `/work/out/artifact.bin` exists and `result.status` is `ok` (a
  `train` run; `validate` keeps none). Up to **24 MiB** it is inline, `artifact_base64`. Larger, up
  to **1 GiB**, it goes first in chunks (below) and the output carries
  `"artifact": {"bytes": N, "sha256": "<64 hex>"}` instead. An artifact over 1 GiB is not sent:
  collect marks the result failed with the finding `run.artifact_size`.
- When the run pod left no `result.json` (stopped, out of memory, past its deadline, or exited
  early), collect sends a failed result with the single finding `run.result` and, when the operator
  passes `--run-exit-code`, that code in the message and in `result.exit_code`.
- `exit_code` is added to the result whenever `--run-exit-code` is given.
- A `409 conflict` whose message says the output is **already** recorded is treated as success (a
  retry after a lost answer), so Lake must answer a second output with `conflict` and a message
  containing "already".

Answer (`value`): `{"recorded": true, "model_id": "…" | null}`.

### `methodology_run_artifact` (collect, an artifact over 24 MiB; lake#690)

Sent in order before the output, 8 MiB of the artifact at a time:

```json
{"run_id": "<uuid>", "offset": 0, "chunk_base64": "…"}
```

Answer (`value`): `{"received": N}`, the bytes Lake now holds. A chunk at an offset below
`received` replaces what Lake held from there (a collect run again starts over safely); an offset
past it is a `conflict`. Collect fails when `received` is not the end of the chunk it sent. Lake
checks the total and the SHA-256 the output names before it records anything.

### `methodology_deployment_input` (stage-model; lake#690)

With the deployment's credential `lmd.<deployment id>.<secret>`, repeated with each answer's
`next_offset` until it is `null`:

```json
{"deployment_id": "<uuid>", "offset": 0, "limit": 8388608}
```

Answer (`value`):

```json
{"spec": {"interface": 1, "deployment_id": "…", "model_id": "…", "methodology_id": "W-…",
          "artifact_format": "pickle|bytes", "features": ["tenure"], "network": false,
          "manifest": {}},
 "chunk_base64": "…", "offset": 0, "next_offset": 8388608,
 "total_bytes": 12000000, "sha256": "<64 hex>"}
```

`stage-model` writes `artifact.bin.partial`, checks the total and the SHA-256, and only then renames
it to `/work/model/artifact.bin` and writes `/work/model/spec.json`.

## `methodology serve` (lake#690)

The serve pod runs the methodology's own image with the model volume read-only at `/work/model`.
Before importing anything of the methodology it makes the run pod's sandbox checks
(`sandbox.credentials`, and `sandbox.network` unless the spec grants network); then it checks the
manifest and interface, loads the artifact (`pickle` is unpickled, `bytes` handed over as bytes),
and listens on `--port` (8080):

| Request | Answer |
|---|---|
| `GET /healthz` | `{"ready": true, "model_id", "methodology_id", "explain"}`: the readiness probe |
| `POST /predict {"rows": [{...}]}` | `{"predictions": [...], "model_id"}`, one per row, from `predict(artifact, data)` |
| `POST /explain {"rows": [{...}]}` | `{"explanations": [...]}`, when the manifest declares `explain` |

`data` is a `Data` with the model's features and no target. At most 10,000 rows and 32 MiB per
request. A refusal is `{"error": {"code", "message"}}`: 400 `invalid_input`, 411, 413 `too_large`,
404 `not_found`/`not_supported`, or 422 `methodology_error` when the code raised. The code is called
one request at a time. Each request logs one line (method, path, status), never the rows. Only
Lake's pods can reach it (the operator's NetworkPolicy); Lake meters and authorizes every call.

## A methodology's image

`templates/methodology.Dockerfile` is the image Lake builds for a methodology, from the
methodology's directory, with the build arguments `METHODOLOGY_RUNTIME_IMAGE` (this image, by
digest) and optionally `METHODOLOGY_PACKAGES` (the lock file's path, default `requirements.lock`):

```dockerfile
ARG METHODOLOGY_RUNTIME_IMAGE
FROM ${METHODOLOGY_RUNTIME_IMAGE}
ARG METHODOLOGY_PACKAGES=requirements.lock
USER root
COPY . /methodology
RUN /opt/orama/bin/install-locked /methodology "$METHODOLOGY_PACKAGES" \
 && chown -R 0:0 /methodology && chmod -R a+rX,go-w /methodology
USER 10001:10001
```

The code and packages are owned by root and read-only to uid 10001, so the run cannot change them.
`install-locked` refuses a lock file that fails the checks in [interface.md](interface.md#the-lock-file).
