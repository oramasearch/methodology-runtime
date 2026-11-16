# SPDX-License-Identifier: MIT

import unittest
from pathlib import Path

from orama_methodology import __version__

ROOT = Path(__file__).resolve().parent.parent


class ReleaseTests(unittest.TestCase):
    def test_the_version_has_release_notes_in_the_index(self):
        notes = ROOT / "docs" / "releases" / f"v{__version__}.md"
        self.assertTrue(notes.is_file(), f"no release notes for v{__version__}")
        self.assertIn(f"v{__version__}", notes.read_text().splitlines()[0])
        self.assertIn(f"(v{__version__}.md)", (ROOT / "docs" / "releases" / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
