import base64
import json
import tempfile
import unittest
from pathlib import Path

from orama_methodology import USER_AGENT
from orama_methodology.cli import main
import hashlib
from unittest import mock

from orama_methodology import transfer
from orama_methodology.transfer import (
    MAX_ARTIFACT_BYTES,
    LakeClient,
    TransferError,
    collect,
    output_body,
    stage,
    stage_model,
)
from tests.fake_lake import CREDENTIAL, DEPLOYMENT_CREDENTIAL, FakeLake
from tests.support import GOOD, manifest, rows

SPEC = {
    "interface": 1,
    "run_id": "00000000-0000-4000-8000-000000000001",
    "mode": "train",
    "methodology_id": "W-0123abcd",
    "params": {},
    "target": "churned",
    "features": ["tenure", "tickets"],
    "holdout_fraction": 0.2,
    "seed": 42,
    "network": False,
    "manifest": manifest(GOOD),
}


def no_sleep(_seconds):
    pass


class StageTests(unittest.TestCase):
    def test_stage_pages_until_next_offset_is_null(self):
        data = rows(23)
        with FakeLake(SPEC, data) as lake, tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "in"
            summary = stage(LakeClient(lake.url, CREDENTIAL, no_sleep), SPEC["run_id"], out, limit=10)
            self.assertEqual(summary["rows"], 23)
            offsets = [body["offset"] for path, _, body in lake.requests]
            self.assertEqual(offsets, [0, 10, 20])
            for _, headers, body in lake.requests:
                self.assertEqual(headers["Authorization"], f"Bearer {CREDENTIAL}")
                self.assertEqual(headers["User-Agent"], USER_AGENT)
                self.assertEqual(body["limit"], 10)
            written = [json.loads(line) for line in (out / "data.jsonl").read_text().splitlines()]
            self.assertEqual(written, data)
            self.assertEqual(json.loads((out / "spec.json").read_text()), SPEC)
            self.assertFalse((out / "data.jsonl.partial").exists())

    def test_stage_retries_a_busy_lake(self):
        with FakeLake(SPEC, rows(5), fail_first=2) as lake, tempfile.TemporaryDirectory() as directory:
            stage(LakeClient(lake.url, CREDENTIAL, no_sleep), SPEC["run_id"], Path(directory))
            self.assertEqual(len(lake.requests), 3)

    def test_a_wrong_credential_is_not_retried(self):
        with FakeLake(SPEC, rows(5)) as lake, tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TransferError) as raised:
                stage(LakeClient(lake.url, "lmr.wrong", no_sleep), SPEC["run_id"], Path(directory))
            self.assertEqual(raised.exception.status, 401)
            self.assertEqual(len(lake.requests), 1)
            self.assertNotIn("lmr.wrong", str(raised.exception))

    def test_the_cli_reads_the_credential_from_its_file(self):
        with FakeLake(SPEC, rows(3)) as lake, tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / "credential"
            credential.write_text(CREDENTIAL + "\n")
            code = main(["stage", "--lake", lake.url, "--run", SPEC["run_id"],
                         "--credential-file", str(credential), "--out", str(Path(directory) / "in")])
            self.assertEqual(code, 0)
            self.assertEqual(len((Path(directory) / "in" / "data.jsonl").read_text().splitlines()), 3)


class CollectTests(unittest.TestCase):
    def _out(self, directory: Path, status: str = "ok", artifact: bytes | None = b"model") -> Path:
        out = directory / "out"
        out.mkdir()
        (out / "result.json").write_text(json.dumps({"status": status, "findings": [], "metrics": {"held_out_auc": 0.8}}))
        if artifact is not None:
            (out / "artifact.bin").write_bytes(artifact)
        (out / "log.txt").write_text("fitting\n")
        return out

    def test_collect_posts_result_artifact_and_log_once(self):
        with FakeLake(SPEC, []) as lake, tempfile.TemporaryDirectory() as directory:
            out = self._out(Path(directory))
            answer = collect(LakeClient(lake.url, CREDENTIAL, no_sleep), SPEC["run_id"], out)
            self.assertEqual(answer["model_id"], "model-1")
            body = lake.outputs[0]
            self.assertEqual(body["run_id"], SPEC["run_id"])
            self.assertEqual(base64.b64decode(body["artifact_base64"]), b"model")
            self.assertEqual(body["log"], "fitting\n")
            self.assertEqual(body["result"]["metrics"], {"held_out_auc": 0.8})
            # Lake accepts the output once; a retry after a lost answer is still a success.
            again = collect(LakeClient(lake.url, CREDENTIAL, no_sleep), SPEC["run_id"], out)
            self.assertTrue(again["already_recorded"])
            self.assertEqual(len(lake.outputs), 1)

    def test_a_failed_run_sends_no_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            body = output_body(SPEC["run_id"], self._out(Path(directory), status="failed"))
            self.assertNotIn("artifact_base64", body)

    def test_a_run_pod_that_left_no_result_is_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "out"
            empty.mkdir()
            body = output_body(SPEC["run_id"], empty, exit_code=137)
            self.assertEqual(body["result"]["status"], "failed")
            self.assertEqual(body["result"]["exit_code"], 137)
            self.assertIn("exit code 137", body["result"]["findings"][0]["message"])
            self.assertNotIn("artifact_base64", body)

    def test_an_artifact_over_the_limit_fails_the_run_instead_of_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            out = self._out(Path(directory), artifact=None)
            with open(out / "artifact.bin", "wb") as handle:
                handle.truncate(MAX_ARTIFACT_BYTES + 1)
            body = output_body(SPEC["run_id"], out)
            self.assertNotIn("artifact_base64", body)
            self.assertEqual(body["result"]["status"], "failed")
            self.assertEqual(body["result"]["findings"][-1]["check"], "run.artifact_size")


class ChunkedArtifactTests(unittest.TestCase):
    """An artifact over the inline limit is sent in chunks before the output (lake#690)."""

    def _out(self, directory: Path, artifact: bytes) -> Path:
        out = directory / "out"
        out.mkdir()
        (out / "result.json").write_text(json.dumps({"status": "ok", "findings": [], "metrics": {}}))
        (out / "artifact.bin").write_bytes(artifact)
        return out

    def test_a_large_artifact_goes_in_ordered_chunks_and_the_output_names_its_digest(self):
        artifact = bytes(range(256)) * 41  # 10,496 bytes
        with mock.patch.object(transfer, "INLINE_ARTIFACT_BYTES", 1000), \
                mock.patch.object(transfer, "ARTIFACT_CHUNK_BYTES", 4000), \
                FakeLake(SPEC, []) as lake, tempfile.TemporaryDirectory() as directory:
            out = self._out(Path(directory), artifact)
            collect(LakeClient(lake.url, CREDENTIAL, no_sleep), SPEC["run_id"], out)
            chunks = [body for path, _, body in lake.requests if path == "/v1/methodology_run_artifact"]
            self.assertEqual([body["offset"] for body in chunks], [0, 4000, 8000])
            self.assertEqual(bytes(lake.artifact), artifact)
            body = lake.outputs[0]
            self.assertNotIn("artifact_base64", body)
            self.assertEqual(body["artifact"], {"bytes": len(artifact), "sha256": hashlib.sha256(artifact).hexdigest()})

    def test_an_artifact_at_the_inline_limit_is_still_inline(self):
        with mock.patch.object(transfer, "INLINE_ARTIFACT_BYTES", 10), tempfile.TemporaryDirectory() as directory:
            body = output_body(SPEC["run_id"], self._out(Path(directory), b"0123456789"))
            self.assertIn("artifact_base64", body)
            self.assertNotIn("artifact", body)

    def test_a_chunk_lake_did_not_take_fails_collect(self):
        class Short:
            def call(self, capability, body):
                return {"received": 1}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.bin"
            path.write_bytes(b"abcdef")
            with self.assertRaises(TransferError):
                transfer.send_artifact(Short(), SPEC["run_id"], path, chunk_bytes=3)


MODEL_SPEC = {
    "interface": 1,
    "deployment_id": "00000000-0000-4000-8000-0000000000d1",
    "model_id": "model-1",
    "methodology_id": "W-0123abcd",
    "artifact_format": "bytes",
    "features": ["tenure"],
    "network": False,
}


class StageModelTests(unittest.TestCase):
    def test_stage_model_pages_the_artifact_and_checks_its_digest(self):
        artifact = b"weights" * 1000
        model = {"spec": MODEL_SPEC, "artifact": artifact}
        with FakeLake(SPEC, [], model=model) as lake, tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "model"
            client = LakeClient(lake.url, DEPLOYMENT_CREDENTIAL, no_sleep)
            summary = stage_model(client, MODEL_SPEC["deployment_id"], out, limit=3000)
            self.assertEqual(summary, {"bytes": len(artifact), "model_id": "model-1"})
            self.assertEqual((out / "artifact.bin").read_bytes(), artifact)
            self.assertEqual(json.loads((out / "spec.json").read_text()), MODEL_SPEC)
            offsets = [body["offset"] for _, _, body in lake.requests]
            self.assertEqual(offsets, [0, 3000, 6000])

    def test_a_wrong_digest_leaves_no_artifact(self):
        model = {"spec": MODEL_SPEC, "artifact": b"weights", "sha256": "0" * 64}
        with FakeLake(SPEC, [], model=model) as lake, tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "model"
            with self.assertRaises(TransferError):
                stage_model(LakeClient(lake.url, DEPLOYMENT_CREDENTIAL, no_sleep), MODEL_SPEC["deployment_id"], out)
            self.assertFalse((out / "artifact.bin").exists())
            self.assertFalse((out / "artifact.bin.partial").exists())

    def test_a_run_credential_does_not_reach_a_deployment(self):
        model = {"spec": MODEL_SPEC, "artifact": b"weights"}
        with FakeLake(SPEC, [], model=model) as lake, tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TransferError) as raised:
                stage_model(LakeClient(lake.url, CREDENTIAL, no_sleep), MODEL_SPEC["deployment_id"], Path(directory))
            self.assertEqual(raised.exception.status, 401)


class EndToEndTests(unittest.TestCase):
    """stage → run → collect against the fake Lake, as the three pods do."""

    def test_the_three_steps(self):
        from orama_methodology.harness import RunOptions, run
        from tests.support import clean_environment, no_network, nothing_exists

        with FakeLake(SPEC, rows(120)) as lake, tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = LakeClient(lake.url, CREDENTIAL, no_sleep)
            stage(client, SPEC["run_id"], root / "in")
            code = run(RunOptions(root / "in", root / "out", GOOD, clean_environment(), nothing_exists, no_network))
            self.assertEqual(code, 0)
            collect(client, SPEC["run_id"], root / "out", exit_code=code)
            body = lake.outputs[0]
            self.assertEqual(body["result"]["status"], "ok")
            self.assertIn("held_out_auc", body["result"]["metrics"])
            self.assertIn("artifact_base64", body)


if __name__ == "__main__":
    unittest.main()
