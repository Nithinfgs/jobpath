"""Data model for workflow runs, parsed from the GitHub REST API shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Step:
    name: str
    number: int
    started: Optional[datetime]
    completed: Optional[datetime]
    conclusion: Optional[str]

    @property
    def duration(self) -> float:
        if self.started and self.completed:
            return max(0.0, (self.completed - self.started).total_seconds())
        return 0.0


@dataclass
class Job:
    id: int
    name: str
    created: datetime
    started: datetime
    completed: datetime
    conclusion: Optional[str]
    runner: str = ""
    steps: List[Step] = field(default_factory=list)
    url: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, (self.completed - self.started).total_seconds())

    @property
    def queue(self) -> float:
        return max(0.0, (self.started - self.created).total_seconds())


@dataclass
class Run:
    id: int
    workflow: str
    event: str
    branch: str
    sha: str
    conclusion: Optional[str]
    url: str
    jobs: List[Job]


def parse_job(raw: Dict[str, Any]) -> Optional[Job]:
    """Return a Job, or None for jobs that never ran (skipped, never started)."""
    created = parse_time(raw.get("created_at"))
    started = parse_time(raw.get("started_at"))
    completed = parse_time(raw.get("completed_at"))
    if raw.get("conclusion") == "skipped" or not (created and started and completed):
        return None
    steps = [
        Step(
            name=s.get("name", ""),
            number=int(s.get("number", 0)),
            started=parse_time(s.get("started_at")),
            completed=parse_time(s.get("completed_at")),
            conclusion=s.get("conclusion"),
        )
        for s in raw.get("steps") or []
    ]
    labels = raw.get("labels") or []
    return Job(
        id=int(raw.get("id", 0)),
        name=raw.get("name", ""),
        created=created,
        started=started,
        completed=completed,
        conclusion=raw.get("conclusion"),
        runner=",".join(labels),
        steps=steps,
        url=raw.get("html_url", ""),
    )


def parse_run(run: Dict[str, Any], jobs: List[Dict[str, Any]]) -> Run:
    parsed = [j for j in (parse_job(raw) for raw in jobs) if j is not None]
    return Run(
        id=int(run.get("id", 0)),
        workflow=run.get("name") or run.get("workflow_name") or "workflow",
        event=run.get("event", ""),
        branch=run.get("head_branch", ""),
        sha=(run.get("head_sha") or "")[:7],
        conclusion=run.get("conclusion"),
        url=run.get("html_url", ""),
        jobs=parsed,
    )
