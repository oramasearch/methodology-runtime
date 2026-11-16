# SPDX-License-Identifier: MIT

"""`methodology serve`: the sandbox checks first, then predictions over HTTP (lake#690)."""

import json
import pickle
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from orama_methodology.harness import RunOptions, run
from orama_methodology.serve import EXIT_BUNDLE, EXIT_SANDBOX, ServeError, load, make_server, serve
from tests.support import GOOD, clean_environment, manifest, no_network, nothing_exists, rows, work


def trained(directory: Path) -> Path:
    """Train the example methodology as a run pod does and stage its model as stage-model does."""
    temporary, in_dir, out_dir = work(GOOD)
    try:
        code = run(RunOptions(in_dir, out_dir, GOOD, clean_environment(), nothing_exists, no_network))
        assert code == 0, (out_dir / "result.json").read_text()
        result = json.loads((out_dir / "result.json").read_text())
        model = directory / "model"
        model.mkdir()
        (model / "artifact.bin").write_bytes((out_dir / "artifact.bin").read_bytes())
        (model / "spec.json").write_text(json.dumps({
            "interface": 1,
            "deployment_id": "00000000-0000-4000-8000-0000000000d1",
            "model_id": "model-1",
            "methodology_id": "W-0123abcd",
            "artifact_format": result["artifact_format"],
            "features": ["tenure", "tickets"],
            "network": False,
            "manifest": manifest(GOOD),
        }))
        return model
    finally:
        temporary.cleanup()


def post(url: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.model_dir = trained(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def _serving(self, model=None):
        model = model or load(self.model_dir, GOOD, clean_environment(), nothing_exists, no_network)
        server = make_server(model, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_it_answers_health_predictions_and_explanations(self):
        url = self._serving()
        with urllib.request.urlopen(url + "/healthz", timeout=10) as response:
            health = json.loads(response.read())
        self.assertEqual(health["model_id"], "model-1")
        self.assertTrue(health["explain"])
        sample = rows(5)
        status, body = post(url + "/predict", {"rows": sample})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["predictions"]), 5)
        self.assertTrue(all(0 <= value <= 1 for value in body["predictions"]))
        status, body = post(url + "/explain", {"rows": sample[:2]})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["explanations"]), 2)

    def test_it_refuses_what_is_not_rows(self):
        url = self._serving()
        status, body = post(url + "/predict", {"rows": "no"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_input")
        status, _ = post(url + "/train", {"rows": []})
        self.assertEqual(status, 404)

    def test_the_code_raising_is_a_methodology_error(self):
        model = load(self.model_dir, GOOD, clean_environment(), nothing_exists, no_network)

        class Raising:
            @staticmethod
            def predict(artifact, data):
                raise RuntimeError("no")

        model.module = Raising
        url = self._serving(model)
        status, body = post(url + "/predict", {"rows": rows(2)})
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "methodology_error")

    def test_a_credential_in_the_environment_stops_it_before_the_code_is_imported(self):
        environment = {**clean_environment(), "LAKE_TOKEN": "not-a-real-credential"}
        with self.assertRaises(ServeError) as raised:
            load(self.model_dir, GOOD, environment, nothing_exists, no_network)
        self.assertEqual(raised.exception.exit_code, EXIT_SANDBOX)
        self.assertNotIn("not-a-real-credential", str(raised.exception))

    def test_network_it_was_not_granted_stops_it(self):
        with self.assertRaises(ServeError) as raised:
            load(self.model_dir, GOOD, clean_environment(), nothing_exists, lambda: ["TCP 1.1.1.1:443"])
        self.assertEqual(raised.exception.exit_code, EXIT_SANDBOX)
        self.assertEqual(raised.exception.findings[1].check, "sandbox.network")

    def test_a_mounted_credential_file_stops_it(self):
        def token_exists(path):
            return path == "/var/run/orama/credential"

        code = serve(self.model_dir, GOOD, "127.0.0.1", 0, clean_environment(), token_exists, no_network)
        self.assertEqual(code, EXIT_SANDBOX)

    def test_no_staged_model_is_a_bundle_error(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(ServeError) as raised:
                load(Path(empty), GOOD, clean_environment(), nothing_exists, no_network)
        self.assertEqual(raised.exception.exit_code, EXIT_BUNDLE)

    def test_a_bytes_artifact_is_handed_over_as_bytes(self):
        spec = json.loads((self.model_dir / "spec.json").read_text())
        artifact = pickle.loads((self.model_dir / "artifact.bin").read_bytes())
        spec["artifact_format"] = "bytes"
        (self.model_dir / "spec.json").write_text(json.dumps(spec))
        model = load(self.model_dir, GOOD, clean_environment(), nothing_exists, no_network)
        self.assertIsInstance(model.artifact, bytes)
        self.assertEqual(pickle.loads(model.artifact), artifact)


if __name__ == "__main__":
    unittest.main()
