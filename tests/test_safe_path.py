"""Stage and collect never import a module the sandbox wrote (methodology-runtime#4).

The run pod writes `/work`, and the image's working directory is `/work`. `python3 -m` puts the
working directory first on `sys.path`, so without care a planted `orama_methodology/` or `json.py`
there would run inside the collect pod, next to the run's credential.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT

COMMAND = ROOT / "bin" / "methodology"
CANARY = "planted-module-ran"


def plant(directory: Path) -> None:
    """Write modules that leave a canary file when imported, where a sandbox could write them."""
    marker = repr(str(directory / CANARY))
    payload = f"open({marker}, 'a').write(__name__ + '\\n')\n"
    package = directory / "orama_methodology"
    package.mkdir()
    (package / "__init__.py").write_text(payload)
    (package / "__main__.py").write_text(payload)
    for name in ("json", "base64", "hashlib", "argparse", "dataclasses", "urllib", "sitecustomize"):
        (directory / f"{name}.py").write_text(payload)


def environment(**extra: str) -> dict:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tempfile.gettempdir(), **extra}


class PlantedModuleTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.work = Path(self._directory.name).resolve()
        plant(self.work)
        (self.work / "out").mkdir()
        self.addCleanup(self._directory.cleanup)

    def assert_nothing_planted_ran(self, completed: subprocess.CompletedProcess) -> None:
        canary = self.work / CANARY
        ran = canary.read_text() if canary.exists() else ""
        self.assertEqual(ran, "", f"a planted module ran: {ran!r}; stderr: {completed.stderr}")

    def run_command(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(COMMAND), *arguments], cwd=self.work, env=environment(), capture_output=True, text=True, timeout=60
        )

    def test_the_command_ignores_modules_in_its_working_directory(self):
        completed = self.run_command("--version")
        self.assert_nothing_planted_ran(completed)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("methodology-runtime", completed.stdout)

    def test_collect_started_in_the_run_volume_imports_nothing_from_it(self):
        completed = self.run_command(
            "collect", "--lake", "http://127.0.0.1:9", "--run", "r", "--credential-file", str(self.work / "none"),
            "--in", str(self.work / "out"),
        )
        self.assert_nothing_planted_ran(completed)
        self.assertEqual(completed.returncode, 4, completed.stderr)  # no credential: a transfer failure

    def test_stage_started_in_the_run_volume_imports_nothing_from_it(self):
        completed = self.run_command(
            "stage", "--lake", "http://127.0.0.1:9", "--run", "r", "--credential-file", str(self.work / "none"),
            "--out", str(self.work / "in"),
        )
        self.assert_nothing_planted_ran(completed)
        self.assertEqual(completed.returncode, 4, completed.stderr)

    def test_the_package_drops_the_working_directory_when_a_launcher_forgets_safe_path(self):
        # Another launcher (a local run, a future operator) starts `python3 -m` without -P. The
        # package cannot stop its own name being shadowed then (only -P can), so no package is
        # planted here; everything it imports after loading must still come from the standard library.
        for planted in (self.work / "orama_methodology").iterdir():
            planted.unlink()
        (self.work / "orama_methodology").rmdir()
        completed = subprocess.run(
            [sys.executable, "-s", "-m", "orama_methodology", "collect", "--lake", "http://127.0.0.1:9", "--run", "r",
             "--credential-file", str(self.work / "none"), "--in", str(self.work / "out")],
            cwd=self.work, env=environment(PYTHONPATH=str(ROOT)), capture_output=True, text=True, timeout=60,
        )
        self.assert_nothing_planted_ran(completed)
        self.assertEqual(completed.returncode, 4, completed.stderr)


class SandboxWritablePathTests(unittest.TestCase):
    def test_the_working_directory_and_the_run_volume_leave_the_path(self):
        from orama_methodology import _without_sandbox_writable

        with tempfile.TemporaryDirectory() as directory:
            work = os.path.realpath(directory)
            lib = "/opt/orama/lib"
            path = ["", work, lib, "/work", "/work/out", "/workspace", "/usr/lib/python3.12"]
            kept = _without_sandbox_writable(path, cwd=work, package_root=lib)
            self.assertEqual(kept, [lib, "/workspace", "/usr/lib/python3.12"])

    def test_the_package_own_directory_stays_even_when_it_is_the_working_directory(self):
        from orama_methodology import _without_sandbox_writable

        self.assertEqual(_without_sandbox_writable(["", str(ROOT)], cwd=str(ROOT), package_root=str(ROOT)),
                         ["", str(ROOT)])


if __name__ == "__main__":
    unittest.main()
