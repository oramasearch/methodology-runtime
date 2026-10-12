"""The `methodology` command: stage, run, collect, stage-model, serve, validate-manifest, install-locked."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .findings import all_ok
from .harness import RunOptions, run
from .manifest import DEFAULT_PACKAGES, check_lock, check_manifest
from .serve import serve
from .transfer import (
    ARTIFACT_CHUNK_BYTES,
    EXIT_OK,
    EXIT_TRANSFER,
    LakeClient,
    TransferError,
    collect,
    read_credential,
    stage,
    stage_model,
)

EXIT_INVALID = 1
EXIT_USAGE = 2


def _client(arguments: argparse.Namespace) -> LakeClient:
    return LakeClient(arguments.lake, read_credential(Path(arguments.credential_file)))


def command_stage(arguments: argparse.Namespace) -> int:
    try:
        summary = stage(_client(arguments), arguments.run, Path(arguments.out), arguments.limit)
    except (TransferError, OSError) as error:
        print(f"stage failed: {error}", file=sys.stderr)
        return EXIT_TRANSFER
    print(json.dumps({"staged": True, **summary}))
    return EXIT_OK


def command_collect(arguments: argparse.Namespace) -> int:
    try:
        answer = collect(_client(arguments), arguments.run, Path(arguments.in_dir), arguments.run_exit_code)
    except (TransferError, OSError) as error:
        print(f"collect failed: {error}", file=sys.stderr)
        return EXIT_TRANSFER
    print(json.dumps({"collected": True, "model_id": answer.get("model_id")}))
    return EXIT_OK


def command_stage_model(arguments: argparse.Namespace) -> int:
    try:
        client = LakeClient(arguments.lake, read_credential(Path(arguments.credential_file)))
        summary = stage_model(client, arguments.deployment, Path(arguments.out), arguments.limit)
    except (TransferError, OSError) as error:
        print(f"stage-model failed: {error}", file=sys.stderr)
        return EXIT_TRANSFER
    print(json.dumps({"staged": True, **summary}))
    return EXIT_OK


def command_serve(arguments: argparse.Namespace) -> int:
    return serve(
        Path(arguments.model),
        Path(arguments.source),
        arguments.host,
        arguments.port,
        dict(os.environ),
        os.path.exists,
    )


def command_run(arguments: argparse.Namespace) -> int:
    code = run(RunOptions(Path(arguments.in_dir), Path(arguments.out), Path(arguments.source)))
    result = json.loads((Path(arguments.out) / "result.json").read_text(encoding="utf-8"))
    print(json.dumps({"status": result["status"], "error": result.get("error")}))
    return code


def _read_manifest(directory: Path) -> dict | None:
    try:
        return json.loads((directory / "methodology.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"cannot read {directory / 'methodology.json'}: {error}", file=sys.stderr)
        return None


def command_validate_manifest(arguments: argparse.Namespace) -> int:
    directory = Path(arguments.directory)
    manifest = _read_manifest(directory)
    if manifest is None:
        return EXIT_INVALID
    findings = check_manifest(manifest)
    packages = manifest.get("packages", DEFAULT_PACKAGES) if isinstance(manifest, dict) else DEFAULT_PACKAGES
    lock = directory / packages if isinstance(packages, str) else None
    if lock is not None and lock.is_file():
        findings += check_lock(lock.read_text(encoding="utf-8"), packages)
    entrypoint = manifest.get("entrypoint", "methodology.py") if isinstance(manifest, dict) else "methodology.py"
    if isinstance(entrypoint, str) and not (directory / entrypoint).is_file():
        from .findings import failed

        findings.append(failed("entrypoint", f"{entrypoint} does not exist"))
    print(json.dumps({"ok": all_ok(findings), "findings": [f.to_json() for f in findings]}, indent=2))
    return EXIT_OK if all_ok(findings) else EXIT_INVALID


def command_install_locked(arguments: argparse.Namespace) -> int:
    """Install a methodology's locked packages into <directory>/.packages, hashes required."""
    directory = Path(arguments.directory)
    packages = arguments.packages
    if packages is None:
        manifest = _read_manifest(directory)
        packages = manifest.get("packages", DEFAULT_PACKAGES) if isinstance(manifest, dict) else DEFAULT_PACKAGES
    lock = directory / packages
    if not lock.is_file():
        print(f"no lock file at {packages}: the methodology uses the standard library only")
        return EXIT_OK
    findings = check_lock(lock.read_text(encoding="utf-8"), packages)
    if not all_ok(findings):
        for finding in findings:
            if not finding.ok:
                print(finding.message, file=sys.stderr)
        return EXIT_INVALID
    if not any(line.strip() and not line.lstrip().startswith("#") for line in lock.read_text().splitlines()):
        print("the lock file is empty: the methodology uses the standard library only")
        return EXIT_OK
    target = directory / ".packages"
    target.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "pip", "install",
        "--require-hashes", "--no-deps", "--only-binary=:all:", "--no-cache-dir",
        "--disable-pip-version-check", "--no-input",
        "--target", str(target), "-r", str(lock),
    ]
    return subprocess.call(command, env={**os.environ, "PIP_NO_INPUT": "1"})


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="methodology", description="Run a Lake workspace methodology.")
    root.add_argument("--version", action="version", version=f"methodology-runtime {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    def transfer_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--lake", default=os.environ.get("LAKE_URL"), required="LAKE_URL" not in os.environ)
        command.add_argument("--run", default=os.environ.get("RUN_ID"), required="RUN_ID" not in os.environ)
        command.add_argument("--credential-file", default="/var/run/orama/credential")

    staged = commands.add_parser("stage", help="page the run's data from Lake into the run volume")
    transfer_arguments(staged)
    staged.add_argument("--out", default="/work/in")
    staged.add_argument("--limit", type=int, default=5000)
    staged.set_defaults(handler=command_stage)

    ran = commands.add_parser("run", help="run the methodology on the staged data (the sandbox)")
    ran.add_argument("--in", dest="in_dir", default="/work/in")
    ran.add_argument("--out", default="/work/out")
    ran.add_argument("--source", default="/methodology")
    ran.set_defaults(handler=command_run)

    collected = commands.add_parser("collect", help="post the run's result and artifact to Lake")
    transfer_arguments(collected)
    collected.add_argument("--in", dest="in_dir", default="/work/out")
    collected.add_argument("--run-exit-code", type=int, default=None,
                           help="the run pod's exit code, reported when it left no result.json")
    collected.set_defaults(handler=command_collect)

    staged_model = commands.add_parser("stage-model", help="page a model deployment's artifact from Lake")
    staged_model.add_argument("--lake", default=os.environ.get("LAKE_URL"), required="LAKE_URL" not in os.environ)
    staged_model.add_argument("--deployment", default=os.environ.get("DEPLOYMENT_ID"),
                              required="DEPLOYMENT_ID" not in os.environ)
    staged_model.add_argument("--credential-file", default="/var/run/orama/credential")
    staged_model.add_argument("--out", default="/work/model")
    staged_model.add_argument("--limit", type=int, default=ARTIFACT_CHUNK_BYTES)
    staged_model.set_defaults(handler=command_stage_model)

    served = commands.add_parser("serve", help="answer predictions from the staged model (the sandbox)")
    served.add_argument("--model", default="/work/model")
    served.add_argument("--source", default="/methodology")
    served.add_argument("--host", default="0.0.0.0")
    served.add_argument("--port", type=int, default=8080)
    served.set_defaults(handler=command_serve)

    validate = commands.add_parser("validate-manifest", help="check a methodology directory locally")
    validate.add_argument("directory", nargs="?", default=".")
    validate.set_defaults(handler=command_validate_manifest)

    install = commands.add_parser("install-locked", help="install the locked packages (image builds)")
    install.add_argument("directory", nargs="?", default="/methodology")
    install.add_argument("packages", nargs="?", default=None)
    install.set_defaults(handler=command_install_locked)
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    return arguments.handler(arguments)


if __name__ == "__main__":
    sys.exit(main())
