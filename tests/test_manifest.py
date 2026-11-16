# SPDX-License-Identifier: MIT

import copy
import unittest

from orama_methodology.manifest import check_lock, check_manifest
from tests.support import BROKEN, GOOD, manifest

HASH = "--hash=sha256:" + "a" * 64


def failures(findings):
    return {finding.check: finding.message for finding in findings if not finding.ok}


class ManifestTests(unittest.TestCase):
    def test_the_good_example_is_valid(self):
        self.assertEqual(failures(check_manifest(manifest(GOOD))), {})

    def test_the_broken_example_manifest_is_valid_but_its_lock_is_not(self):
        self.assertEqual(failures(check_manifest(manifest(BROKEN))), {})
        lock = failures(check_lock((BROKEN / "requirements.lock").read_text()))
        self.assertIn("lock.line1", lock)
        self.assertIn("numpy is not pinned", lock["lock.line1"])

    def test_every_problem_is_reported_at_once(self):
        bad = {
            "interface": 2,
            "name": "",
            "entrypoint": "../escape.py",
            "inputs": {"kinds": ["spreadsheet"], "target": {"required": False, "kind": "binary"}},
            "parameters": [
                {"name": "depth", "kind": "int", "default": 99, "minimum": 1, "maximum": 16},
                {"name": "rate", "kind": "float", "default": 0.1, "log": True},
                {"name": "mode", "kind": "categorical", "default": "c", "choices": ["a", "b"]},
                {"name": "flag", "kind": "bool", "default": "yes"},
                {"name": "depth", "kind": "int", "default": 1},
            ],
            "metrics": ["held_out_auc"],
            "resources": {"cpu": "lots", "memory": "2GB", "gpu": -1, "timeout_seconds": 0},
            "colour": "blue",
        }
        found = failures(check_manifest(bad))
        for check in (
            "manifest.interface",
            "manifest.name",
            "manifest.entrypoint",
            "manifest.inputs.kinds",
            "manifest.inputs.target",
            "manifest.parameters",
            "manifest.parameters.depth",
            "manifest.parameters.rate",
            "manifest.parameters.mode",
            "manifest.parameters.flag",
            "manifest.metrics",
            "manifest.resources.cpu",
            "manifest.resources.memory",
            "manifest.resources.gpu",
            "manifest.resources.timeout_seconds",
            "manifest",
        ):
            self.assertIn(check, found, check)
        self.assertIn("base names", found["manifest.metrics"])

    def test_a_methodology_with_no_target(self):
        clustering = copy.deepcopy(manifest(GOOD))
        clustering["inputs"]["target"] = {"required": False, "kind": "none"}
        self.assertEqual(failures(check_manifest(clustering)), {})
        clustering["inputs"]["target"] = {"required": True, "kind": "none"}
        self.assertIn("manifest.inputs.target", failures(check_manifest(clustering)))


class LockTests(unittest.TestCase):
    def test_empty_and_comment_only_locks_mean_the_standard_library(self):
        for text in ("", "# nothing\n\n"):
            findings = check_lock(text)
            self.assertTrue(all(f.ok for f in findings))
            self.assertIn("standard library", findings[-1].message)

    def test_pinned_and_hashed_requirements_pass(self):
        text = (
            f"numpy==2.1.3 \\\n    {HASH} \\\n    --hash=sha256:{'b' * 64}\n"
            f'colorama==0.4.6 ; sys_platform == "win32" {HASH}\n'
            f"scikit-learn[extra]==1.5.2 {HASH}  # trailing comment\n"
        )
        findings = check_lock(text)
        self.assertEqual(failures(findings), {})
        self.assertIn("3 pinned packages", findings[-1].message)

    def test_unpinned_unhashed_and_option_lines_fail(self):
        text = (
            "requests\n"
            "numpy>=2\n"
            "pandas==2.2.3\n"
            f"scipy==1.14.1 --hash=md5:{'a' * 32}\n"
            "--index-url https://example.invalid/simple\n"
            "-e git+https://example.invalid/x.git\n"
            f"torch==2.5.0 {HASH} --pre\n"
        )
        found = failures(check_lock(text))
        self.assertEqual(sorted(found), [f"lock.line{n}" for n in range(1, 8)])
        self.assertIn("has no --hash", found["lock.line3"])


if __name__ == "__main__":
    unittest.main()
