"""Shared helpers: the examples, a synthetic dataset and a staged /work directory."""

import json
import math
import random
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
GOOD = EXAMPLES / "logistic"
BROKEN = EXAMPLES / "broken"


def manifest(directory: Path) -> dict:
    return json.loads((directory / "methodology.json").read_text())


def rows(count: int = 200, seed: int = 7) -> list[dict]:
    """A separable-ish binary problem: churned depends on tenure and tickets."""
    generator = random.Random(seed)
    data = []
    for index in range(count):
        tenure = generator.uniform(0, 60)
        tickets = generator.randint(0, 10)
        plan = generator.choice(["basic", "pro"])
        logit = 1.5 - 0.08 * tenure + 0.4 * tickets + generator.gauss(0, 0.5)
        churned = 1 if 1 / (1 + math.exp(-logit)) > 0.5 else 0
        data.append({"customer": f"c{index}", "tenure": tenure, "tickets": tickets, "plan": plan, "churned": churned})
    return data


def work(source: Path, mode: str = "train", network: bool = False, data: list[dict] | None = None, **extra):
    """A temporary /work with in/spec.json and in/data.jsonl; returns (tempdir, in, out)."""
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    in_dir, out_dir = root / "in", root / "out"
    in_dir.mkdir()
    spec = {
        "interface": 1,
        "run_id": "00000000-0000-4000-8000-000000000001",
        "mode": mode,
        "methodology_id": "W-0123abcd",
        "params": {},
        "target": "churned",
        "features": ["tenure", "tickets"],
        "holdout_fraction": 0.25,
        "seed": 42,
        "network": network,
        "manifest": manifest(source),
    }
    spec.update(extra)
    (in_dir / "spec.json").write_text(json.dumps(spec))
    with open(in_dir / "data.jsonl", "w") as handle:
        for row in rows() if data is None else data:
            handle.write(json.dumps(row) + "\n")
    return directory, in_dir, out_dir


def no_network() -> list[str]:
    return []


def clean_environment() -> dict:
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "KUBERNETES_SERVICE_HOST": "10.0.0.1"}


def nothing_exists(_path: str) -> bool:
    return False
