"""One finding: a named check, whether it passed, and what to do about it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    check: str
    ok: bool
    message: str

    def to_json(self) -> dict:
        return {"check": self.check, "ok": self.ok, "message": self.message}


def passed(check: str, message: str = "") -> Finding:
    return Finding(check, True, message)


def failed(check: str, message: str) -> Finding:
    return Finding(check, False, message)


def all_ok(findings: list[Finding]) -> bool:
    return all(finding.ok for finding in findings)
