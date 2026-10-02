from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from jobpath.model import Job, Run, Step

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def job(
    name: str,
    created: float,
    started: float,
    completed: float,
    steps: Optional[List[Tuple[str, float, float]]] = None,
    conclusion: str = "success",
    jid: Optional[int] = None,
) -> Job:
    return Job(
        id=jid if jid is not None else abs(hash(name)) % 10**9,
        name=name,
        created=at(created),
        started=at(started),
        completed=at(completed),
        conclusion=conclusion,
        steps=[Step(n, i + 1, at(s), at(e), "success") for i, (n, s, e) in enumerate(steps or [])],
    )


def run(jobs: List[Job], rid: int = 1) -> Run:
    return Run(
        id=rid,
        workflow="CI",
        event="push",
        branch="main",
        sha="abc1234",
        conclusion="success",
        url="",
        jobs=jobs,
    )


def diamond() -> Run:
    """lint(0-10) -> build(11-41) -> test(42-102); docs(0-50) in parallel; deploy needs test and docs."""
    return run(
        [
            job("lint", 0, 1, 10),
            job("docs", 0, 1, 50),
            job("build", 10, 11, 41),
            job(
                "test",
                41,
                42,
                102,
                steps=[
                    ("Set up job", 42, 44),
                    ("Run actions/checkout@v4", 44, 49),
                    ("Run actions/setup-node@v4", 49, 55),
                    ("Run npm ci", 55, 70),
                    ("Run npm test", 70, 100),
                    ("Post Run actions/checkout@v4", 100, 101),
                ],
            ),
            job("deploy", 102, 104, 120),
        ]
    )
