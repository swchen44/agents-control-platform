"""Evidence-based stop (report §9.3-2): deterministic graders decide DONE.

Why this exists (measured, not theoretical): codex exits rc=0 on SIGTERM, so
"terminal event OR exit code" judges a half-finished run as completed — exit
codes prove the *process* ended, never that the *task* did. The only trustworthy
completion signal is evidence a grader can check deterministically: files that
must exist with the right content, a test command that must pass.

Graders run against the run's working directory and return a Verdict with
human-readable reasons (these go into the journal, so a failed check is
auditable later). Compose with AllOf. The Supervisor consumes this via its
optional ``grader`` argument: a run whose worker claims success but whose
evidence fails is overridden to FAILED — the one sanctioned override of a
sticky terminal state, because evidence outranks self-report.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else (
            "ok" if self.passed else "failed")


class Grader(Protocol):
    name: str
    def grade(self, workdir: str) -> Verdict: ...


class FileChecklistGrader:
    """Every listed file must exist; a non-None value must match its stripped
    content exactly. This is the grader recovery_test.py's C3 check embodies."""

    name = "files"

    def __init__(self, expected: dict[str, str | None]):
        self.expected = expected

    def grade(self, workdir: str) -> Verdict:
        reasons: list[str] = []
        for rel, want in self.expected.items():
            path = os.path.join(workdir, rel)
            if not os.path.isfile(path):
                reasons.append(f"missing {rel}")
                continue
            if want is not None:
                got = open(path).read().strip()
                if got != want:
                    reasons.append(f"{rel}: expected {want!r}, got {got!r}")
        return Verdict(passed=not reasons,
                       reasons=reasons or [f"{len(self.expected)} file(s) verified"])


class CommandGrader:
    """Run a command in the workdir; exit code 0 is the evidence (e.g. a test
    suite). Output tails are kept in the verdict for the journal."""

    name = "command"

    def __init__(self, argv: list[str], timeout: float = 60.0):
        self.argv = argv
        self.timeout = timeout

    def grade(self, workdir: str) -> Verdict:
        try:
            proc = subprocess.run(self.argv, cwd=workdir, capture_output=True,
                                  text=True, timeout=self.timeout)
        except (subprocess.TimeoutExpired, OSError) as e:
            return Verdict(False, [f"{' '.join(self.argv)}: {e}"])
        if proc.returncode == 0:
            return Verdict(True, [f"{' '.join(self.argv)} rc=0"])
        tail = (proc.stdout + proc.stderr)[-200:].strip()
        return Verdict(False, [f"{' '.join(self.argv)} rc={proc.returncode}: {tail}"])


class AllOf:
    """All sub-graders must pass; reasons are concatenated for the journal."""

    name = "all-of"

    def __init__(self, *graders: Grader):
        self.graders = graders

    def grade(self, workdir: str) -> Verdict:
        reasons: list[str] = []
        passed = True
        for g in self.graders:
            v = g.grade(workdir)
            passed = passed and v.passed
            reasons.extend(f"[{g.name}] {r}" for r in v.reasons)
        return Verdict(passed, reasons)
