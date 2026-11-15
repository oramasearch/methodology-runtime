# SPDX-License-Identifier: MIT

import json
import pickle
import shutil
import tempfile
import unittest
from pathlib import Path

from orama_methodology.harness import EXIT_FAILED, EXIT_OK, EXIT_SANDBOX, RunOptions, run, split_indices
from tests.support import BROKEN, GOOD, clean_environment, no_network, nothing_exists, work


def options(in_dir, out_dir, source, **overrides):
    values = dict(
        environment=clean_environment(),
        credential_exists=nothing_exists,
        network_probe=no_network,
    )
    values.update(overrides)
    return RunOptions(in_dir, out_dir, source, **values)


def result_of(out_dir: Path) -> dict:
    return json.loads((out_dir / "result.json").read_text())


def failing(result: dict) -> dict:
    return {f["check"]: f["message"] for f in result["findings"] if not f["ok"]}


class RunTests(unittest.TestCase):
    def test_train_writes_metrics_artifact_and_log(self):
        directory, in_dir, out_dir = work(GOOD, "train", params={"epochs": 150})
        with directory:
            self.assertEqual(run(options(in_dir, out_dir, GOOD)), EXIT_OK)
            result = result_of(out_dir)
            self.assertEqual(result["status"], "ok", failing(result))
            self.assertEqual(result["rows"], {"train": 150, "held_out": 50})
            for name in ("auc", "accuracy", "log_loss"):
                self.assertIn(f"in_sample_{name}", result["metrics"])
                self.assertIn(f"held_out_{name}", result["metrics"])
            self.assertGreater(result["metrics"]["held_out_auc"], 0.8)
            self.assertTrue(result["explain_supported"])
            self.assertEqual(result["artifact_format"], "pickle")
            model = pickle.loads((out_dir / "artifact.bin").read_bytes())
            self.assertEqual(model["features"], ["tenure", "tickets"])
            self.assertEqual(len(result["predictions_sample"]), 20)
            self.assertTrue((out_dir / "log.txt").exists())
            self.assertEqual([p.name for p in out_dir.iterdir() if p.name.startswith("methodology-log")], [])

    def test_validate_checks_everything_but_keeps_no_artifact(self):
        directory, in_dir, out_dir = work(GOOD, "validate", data=None)
        with directory:
            self.assertEqual(run(options(in_dir, out_dir, GOOD)), EXIT_OK)
            result = result_of(out_dir)
            self.assertEqual(result["status"], "ok")
            self.assertFalse((out_dir / "artifact.bin").exists())
            self.assertIn("held_out_auc", result["metrics"])

    def test_the_broken_example_reports_every_missing_item(self):
        directory, in_dir, out_dir = work(BROKEN, "validate")
        with directory:
            self.assertEqual(run(options(in_dir, out_dir, BROKEN)), EXIT_FAILED)
            result = result_of(out_dir)
            found = failing(result)
            self.assertEqual(result["status"], "failed")
            self.assertIn("interface.score", found)
            self.assertIn("score(artifact, data) is missing", found["interface.score"])
            self.assertIn("lock.line1", found)
            self.assertFalse(any(f["check"] == "run.fit" for f in result["findings"]))

    def test_code_that_raises_is_reported_with_its_step_and_log(self):
        source = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, source)
        shutil.copy(GOOD / "methodology.json", source)
        shutil.copy(GOOD / "requirements.lock", source)
        (source / "methodology.py").write_text(
            "import sys\n"
            "def fit(data, params):\n    print('fitting'); print('bad', file=sys.stderr); raise ValueError('no')\n"
            "def predict(artifact, data):\n    return []\n"
            "def score(artifact, data):\n    return {}\n"
            "def explain(artifact, data):\n    return []\n"
        )
        directory, in_dir, out_dir = work(source, "train")
        with directory:
            self.assertEqual(run(options(in_dir, out_dir, source)), EXIT_FAILED)
            result = result_of(out_dir)
            self.assertIn("run.fit", failing(result))
            self.assertIn("ValueError", failing(result)["run.fit"])
            log = (out_dir / "log.txt").read_text()
            self.assertIn("fitting", log)
            self.assertIn("bad", log)
            self.assertIn("Traceback", log)

    def test_scores_must_be_finite_numbers_for_every_metric(self):
        source = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, source)
        shutil.copy(GOOD / "methodology.json", source)
        shutil.copy(GOOD / "requirements.lock", source)
        (source / "methodology.py").write_text(
            "def fit(data, params):\n    return b'model'\n"
            "def predict(artifact, data):\n    return [0.5] * len(data)\n"
            "def score(artifact, data):\n    return {'auc': float('nan'), 'accuracy': 'high'}\n"
            "def explain(artifact, data):\n    return [{'tenure': 1.0}] * len(data)\n"
        )
        directory, in_dir, out_dir = work(source, "train")
        with directory:
            self.assertEqual(run(options(in_dir, out_dir, source)), EXIT_FAILED)
            found = failing(result_of(out_dir))
            self.assertIn("log_loss", found["score.held_out"])
            self.assertIn("not finite numbers", "".join(found.values()))

    def test_bytes_artifacts_are_written_as_they_are(self):
        source = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, source)
        written = dict(json.loads((GOOD / "methodology.json").read_text()), explain=False)
        (source / "methodology.json").write_text(json.dumps(written))
        shutil.copy(GOOD / "requirements.lock", source)
        (source / "methodology.py").write_text(
            "def fit(data, params):\n    return b'raw-model'\n"
            "def predict(artifact, data):\n    return [0.5] * len(data)\n"
            "def score(artifact, data):\n    return {'auc': 0.5, 'accuracy': 0.5, 'log_loss': 0.69}\n"
        )
        directory, in_dir, out_dir = work(source, "train", manifest=written)
        with directory:
            self.assertEqual(run(options(in_dir, out_dir, source)), EXIT_OK)
            self.assertEqual((out_dir / "artifact.bin").read_bytes(), b"raw-model")
            self.assertEqual(result_of(out_dir)["artifact_format"], "bytes")

    def test_parameters_and_data_are_checked_against_the_manifest(self):
        directory, in_dir, out_dir = work(
            GOOD, "train", params={"epochs": 0, "depth": 3}, target="missing_column"
        )
        with directory:
            self.assertEqual(run(options(in_dir, out_dir, GOOD)), EXIT_FAILED)
            found = failing(result_of(out_dir))
            self.assertIn("params.epochs", found)
            self.assertIn("params.depth", found)
            self.assertIn("data.target", found)

    def test_the_split_is_seeded_and_holds_some_out(self):
        self.assertEqual(split_indices(10, 0.2, 1), split_indices(10, 0.2, 1))
        train, held = split_indices(10, 0.2, 1)
        self.assertEqual((len(train), len(held)), (8, 2))
        self.assertEqual(sorted(train + held), list(range(10)))
        self.assertEqual(len(split_indices(2, 0.01, 1)[1]), 1)


class SandboxRefusalTests(unittest.TestCase):
    """The run refuses before the person's code is imported."""

    def _canary_source(self) -> Path:
        source = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, source)
        shutil.copytree(GOOD, source, dirs_exist_ok=True)
        canary = source / "imported.txt"
        (source / "methodology.py").write_text(
            f"open({str(canary)!r}, 'w').write('imported')\n" + (GOOD / "methodology.py").read_text()
        )
        return source

    def test_a_credential_in_the_environment_refuses_the_run(self):
        source = self._canary_source()
        directory, in_dir, out_dir = work(source)
        with directory:
            environment = dict(clean_environment(), LAKE_TOKEN="lmr.should-never-be-here")
            code = run(options(in_dir, out_dir, source, environment=environment))
            self.assertEqual(code, EXIT_SANDBOX)
            result = result_of(out_dir)
            self.assertIn("LAKE_TOKEN", failing(result)["sandbox.credentials"])
            self.assertNotIn("should-never-be-here", json.dumps(result))
            self.assertFalse((source / "imported.txt").exists())

    def test_a_service_account_token_file_refuses_the_run(self):
        source = self._canary_source()
        directory, in_dir, out_dir = work(source)
        token = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        with directory:
            code = run(options(in_dir, out_dir, source, credential_exists=lambda path: path == token))
            self.assertEqual(code, EXIT_SANDBOX)
            self.assertIn(token, failing(result_of(out_dir))["sandbox.credentials"])
            self.assertFalse((source / "imported.txt").exists())

    def test_network_it_was_not_granted_refuses_the_run(self):
        source = self._canary_source()
        directory, in_dir, out_dir = work(source, network=False)
        with directory:
            code = run(options(in_dir, out_dir, source, network_probe=lambda: ["TCP 1.1.1.1:443"]))
            self.assertEqual(code, EXIT_SANDBOX)
            self.assertIn("1.1.1.1", failing(result_of(out_dir))["sandbox.network"])
            self.assertFalse((source / "imported.txt").exists())

    def test_granted_network_is_not_probed(self):
        source = self._canary_source()
        directory, in_dir, out_dir = work(source, network=True)

        def probe():
            raise AssertionError("a granted run is not probed")

        with directory:
            self.assertEqual(run(options(in_dir, out_dir, source, network_probe=probe)), EXIT_OK)
            self.assertTrue((source / "imported.txt").exists())


if __name__ == "__main__":
    unittest.main()
