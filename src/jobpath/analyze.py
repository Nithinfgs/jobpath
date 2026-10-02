"""Critical-path analysis of workflow runs.

The Actions API does not expose the `needs:` graph, but it does expose job
timestamps. A job is created the moment its last dependency finishes, so the
jobs that completed just before a job's `created_at` are its dependencies. That
is enough to rebuild the dependency graph, walk back from the last job to
finish, and re-simulate the run to answer "what if this job were faster?".
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from .model import Job, Run

CATEGORIES = (
    "queue",
    "orchestration",
    "runner",
    "checkout",
    "toolchain",
    "cache",
    "deps",
    "artifacts",
    "work",
)
SETUP_CATEGORIES = ("checkout", "toolchain", "cache", "deps")

# Seconds between a dependency finishing and its dependent being created.
DEFAULT_TOLERANCE = 5.0
# Jobs created this soon after the first job are treated as roots.
ROOT_WINDOW = 2.0

_DEPS = re.compile(
    r"\b(npm (ci|install|i)|yarn (install|--frozen)|pnpm (install|i)|pip3? install|"
    r"uv (sync|pip install)|poetry install|bundle install|cargo fetch|go mod download|"
    r"composer install|mvn .*dependency|gradle .*dependencies|install dependencies)",
    re.I,
)
_TOOLCHAIN = re.compile(
    r"(setup-[\w-]+|install (rust|go|node|python|java|ruby|uv|toolchain)|rustup|"
    r"toolchain|set up (python|node|go|java|ruby)|setup (python|node|go|java|ruby))",
    re.I,
)


def classify_step(name: str) -> str:
    n = name.strip()
    low = n.lower()
    if low in ("set up job", "complete job", "initialize containers", "stop containers"):
        return "runner"
    if "checkout" in low:
        return "checkout"
    if "cache" in low:
        return "cache"
    if low.startswith("post "):
        return "runner"
    if "upload-artifact" in low or "download-artifact" in low or "artifact" in low:
        return "artifacts"
    if _DEPS.search(n):
        return "deps"
    if _TOOLCHAIN.search(n):
        return "toolchain"
    return "work"


_VERSION = re.compile(r"@[\w.\-/]+")


def normalize_step(name: str) -> str:
    """Drop pinned refs so `actions/checkout@v4` and `@<sha>` group together."""
    return _VERSION.sub("", name).strip()


@dataclass
class PathNode:
    job: Job
    latency: float
    orchestration: float
    queue: float
    categories: Dict[str, float]

    @property
    def duration(self) -> float:
        return self.job.duration


@dataclass
class RunAnalysis:
    run: Run
    start: datetime
    wall: float
    path: List[PathNode]
    preds: Dict[int, List[Job]]
    loose: List[str]
    job_seconds: float
    setup_seconds: float
    path_categories: Dict[str, float]
    gains: Dict[int, Gain]

    def offset(self, t: datetime) -> float:
        return (t - self.start).total_seconds()

    @property
    def parallelism(self) -> float:
        return self.job_seconds / self.wall if self.wall else 0.0


@dataclass
class Gain:
    """Wall-time saved if a critical-path job got faster (simulated).

    Timestamps cannot always tell which of several jobs finishing in the same
    second gated a dependent, so each figure is a range: `*` assumes every
    near-simultaneous finisher gates (lower bound), `*_max` assumes only the
    last one does (upper bound).
    """

    faster_30: float
    faster_30_max: float
    removed: float
    removed_max: float

    @property
    def mid_30(self) -> float:
        return (self.faster_30 + self.faster_30_max) / 2


def _job_categories(job: Job) -> Dict[str, float]:
    cats: Dict[str, float] = {c: 0.0 for c in CATEGORIES}
    stepped = 0.0
    for step in job.steps:
        d = step.duration
        stepped += d
        cats[classify_step(step.name)] += d
    cats["runner"] += max(0.0, job.duration - stepped)
    return cats


def _infer_preds(
    jobs: List[Job], start: datetime, tolerance: float
) -> Tuple[Dict[int, List[Job]], List[str]]:
    preds: Dict[int, List[Job]] = {}
    loose: List[str] = []
    for j in jobs:
        if (j.created - start).total_seconds() <= ROOT_WINDOW:
            preds[j.id] = []
            continue
        found = [
            p
            for p in jobs
            if p is not j
            and p.started < j.started
            and -1.0 <= (j.created - p.completed).total_seconds() <= tolerance
        ]
        if not found:
            earlier = [
                p
                for p in jobs
                if p is not j and p.started < j.started and (j.created - p.completed).total_seconds() >= -1.0
            ]
            if earlier:
                found = [max(earlier, key=lambda p: p.completed)]
                loose.append(j.name)
        preds[j.id] = found
    return preds, loose


def _simulate(
    jobs: List[Job],
    preds: Dict[int, List[Job]],
    start: datetime,
    scale: Dict[int, float],
) -> float:
    """Wall time if jobs in `scale` run for `scale[id]` x their duration."""
    finish: Dict[int, float] = {}
    for j in sorted(jobs, key=lambda x: x.started):
        ps = preds[j.id]
        if ps:
            latency = (j.started - max(p.completed for p in ps)).total_seconds()
            begin = max(finish[p.id] for p in ps) + latency
        else:
            begin = (j.started - start).total_seconds()
        finish[j.id] = begin + j.duration * scale.get(j.id, 1.0)
    return max(finish.values())


def analyze_run(run: Run, tolerance: float = DEFAULT_TOLERANCE) -> Optional[RunAnalysis]:
    jobs = list(run.jobs)
    if not jobs:
        return None
    start = min(j.created for j in jobs)
    preds, loose = _infer_preds(jobs, start, tolerance)
    last = max(jobs, key=lambda j: j.completed)
    wall = (last.completed - start).total_seconds()

    chain: List[PathNode] = []
    cur: Optional[Job] = last
    while cur is not None:
        ps = preds[cur.id]
        crit = max(ps, key=lambda p: p.completed) if ps else None
        anchor = crit.completed if crit else start
        chain.append(
            PathNode(
                job=cur,
                latency=(cur.started - anchor).total_seconds(),
                orchestration=max(0.0, (cur.created - anchor).total_seconds()),
                queue=cur.queue,
                categories=_job_categories(cur),
            )
        )
        cur = crit
    chain.reverse()

    path_cats: Dict[str, float] = {c: 0.0 for c in CATEGORIES}
    for node in chain:
        path_cats["orchestration"] += node.orchestration
        path_cats["queue"] += max(0.0, node.latency - node.orchestration)
        for c, v in node.categories.items():
            path_cats[c] += v

    chain_preds = {jid: ([max(ps, key=lambda p: p.completed)] if ps else []) for jid, ps in preds.items()}
    gains: Dict[int, Gain] = {}
    for node in chain:
        j = node.job

        def saved(p: Dict[int, List[Job]], factor: float, jid: int = j.id) -> float:
            return max(0.0, wall - _simulate(jobs, p, start, {jid: factor}))

        gains[j.id] = Gain(
            faster_30=saved(preds, 0.7),
            faster_30_max=saved(chain_preds, 0.7),
            removed=saved(preds, 0.0),
            removed_max=saved(chain_preds, 0.0),
        )

    job_seconds = sum(j.duration for j in jobs)
    setup = sum(sum(_job_categories(j)[c] for c in SETUP_CATEGORIES) for j in jobs)
    return RunAnalysis(
        run=run,
        start=start,
        wall=wall,
        path=chain,
        preds=preds,
        loose=loose,
        job_seconds=job_seconds,
        setup_seconds=setup,
        path_categories=path_cats,
        gains=gains,
    )


# ---------------------------------------------------------------- aggregation


def _p90(values: Sequence[float]) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(0.9 * len(s)) - 1)]


@dataclass
class JobStat:
    name: str
    appearances: int
    critical: int
    median_duration: float
    p90_duration: float
    median_queue: float
    median_gain_30: float
    median_gain_30_max: float
    median_gain_removed: float
    median_gain_removed_max: float
    median_share: float  # of wall time, over runs where the job is critical
    expected_gain_30: float  # mean over all runs of the mid-point saving (0 when off-path)


@dataclass
class StepStat:
    job: str
    step: str
    category: str
    median_duration: float
    critical: int


@dataclass
class Summary:
    analyses: List[RunAnalysis]
    median_wall: float
    p90_wall: float
    median_job_seconds: float
    median_parallelism: float
    setup_share: float
    path_categories: Dict[str, float]
    jobs: List[JobStat] = field(default_factory=list)
    steps: List[StepStat] = field(default_factory=list)
    loose_jobs: List[str] = field(default_factory=list)

    @property
    def typical(self) -> RunAnalysis:
        """The run whose wall time is closest to the median."""
        return min(self.analyses, key=lambda a: abs(a.wall - self.median_wall))


def summarize(analyses: List[RunAnalysis]) -> Summary:
    walls = [a.wall for a in analyses]
    med = statistics.median
    total_job = sum(a.job_seconds for a in analyses) or 1.0
    path_cats = {c: med([a.path_categories[c] for a in analyses]) for c in CATEGORIES}

    durations: Dict[str, List[float]] = {}
    queues: Dict[str, List[float]] = {}
    crit: Dict[str, List[RunAnalysis]] = {}
    shares: Dict[str, List[float]] = {}
    gain30: Dict[str, List[float]] = {}
    gain30_max: Dict[str, List[float]] = {}
    gain_rm: Dict[str, List[float]] = {}
    gain_rm_max: Dict[str, List[float]] = {}
    gain_mid: Dict[str, List[float]] = {}
    steps: Dict[Tuple[str, str], List[float]] = {}
    step_cat: Dict[Tuple[str, str], str] = {}

    for a in analyses:
        for j in a.run.jobs:
            durations.setdefault(j.name, []).append(j.duration)
            queues.setdefault(j.name, []).append(j.queue)
        for node in a.path:
            n = node.job.name
            crit.setdefault(n, []).append(a)
            shares.setdefault(n, []).append(node.duration / a.wall if a.wall else 0.0)
            g = a.gains[node.job.id]
            gain30.setdefault(n, []).append(g.faster_30)
            gain30_max.setdefault(n, []).append(g.faster_30_max)
            gain_rm.setdefault(n, []).append(g.removed)
            gain_rm_max.setdefault(n, []).append(g.removed_max)
            gain_mid.setdefault(n, []).append(g.mid_30)
            for s in node.job.steps:
                key = (n, normalize_step(s.name))
                steps.setdefault(key, []).append(s.duration)
                step_cat[key] = classify_step(s.name)

    jobs = [
        JobStat(
            name=n,
            appearances=len(durations[n]),
            critical=len(crit.get(n, [])),
            median_duration=med(durations[n]),
            p90_duration=_p90(durations[n]),
            median_queue=med(queues[n]),
            median_gain_30=med(gain30[n]) if n in gain30 else 0.0,
            median_gain_30_max=med(gain30_max[n]) if n in gain30_max else 0.0,
            median_gain_removed=med(gain_rm[n]) if n in gain_rm else 0.0,
            median_gain_removed_max=med(gain_rm_max[n]) if n in gain_rm_max else 0.0,
            median_share=med(shares[n]) if n in shares else 0.0,
            expected_gain_30=sum(gain_mid.get(n, [])) / len(analyses),
        )
        for n in durations
    ]
    jobs.sort(key=lambda s: (-s.expected_gain_30, -s.critical, -s.median_duration))

    step_stats = [
        StepStat(job=k[0], step=k[1], category=step_cat[k], median_duration=med(v), critical=len(v))
        for k, v in steps.items()
    ]
    step_stats.sort(key=lambda s: -s.median_duration * s.critical)

    loose = sorted({n for a in analyses for n in a.loose})
    return Summary(
        analyses=analyses,
        median_wall=med(walls),
        p90_wall=_p90(walls),
        median_job_seconds=med([a.job_seconds for a in analyses]),
        median_parallelism=med([a.parallelism for a in analyses]),
        setup_share=sum(a.setup_seconds for a in analyses) / total_job,
        path_categories=path_cats,
        jobs=jobs,
        steps=step_stats,
        loose_jobs=loose,
    )


# ------------------------------------------------------------------- insights


def fmt_dur(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def fmt_range(lo: float, hi: float) -> str:
    """`46s`, or `0s–46s` when the two bounds differ noticeably."""
    if hi - lo < 3:
        return fmt_dur(hi)
    return f"{fmt_dur(lo)}–{fmt_dur(hi)}"


def insights(summary: Summary) -> List[str]:
    out: List[str] = []
    n = len(summary.analyses)
    wall = summary.median_wall
    if wall <= 0:
        return out

    top = summary.jobs[0] if summary.jobs and summary.jobs[0].expected_gain_30 >= 1 else None
    if top:
        out.append(
            f"Long pole: '{top.name}' is on the critical path in {top.critical}/{n} runs "
            f"(median {fmt_dur(top.median_duration)}, {top.median_share:.0%} of wall time). "
            f"Making it 30% faster would save {fmt_range(top.median_gain_30, top.median_gain_30_max)} "
            f"when it is on the path."
        )
        if top.median_gain_removed_max + 1 < top.median_duration * 0.9:
            out.append(
                f"Even removing '{top.name}' entirely would save at most "
                f"{fmt_dur(top.median_gain_removed_max)}: a parallel branch is nearly as slow, "
                f"so both need work."
            )

    cats = summary.path_categories
    queue = cats["queue"] + cats["orchestration"]
    if queue / wall >= 0.08 and queue >= 15:
        out.append(
            f"Waiting, not running: {fmt_dur(queue)} ({queue / wall:.0%}) of the critical path "
            f"is queue/scheduling time. Check runner availability or concurrency limits."
        )
    setup_path = sum(cats[c] for c in SETUP_CATEGORIES)
    if setup_path / wall >= 0.15:
        out.append(
            f"Setup on the critical path: checkout, toolchains, caches and dependency installs "
            f"take {fmt_dur(setup_path)} ({setup_path / wall:.0%}) of the critical path."
        )
    if summary.setup_share >= 0.2:
        out.append(
            f"Setup tax: {summary.setup_share:.0%} of all job time is checkout, toolchain, "
            f"cache and dependency steps, repeated in every job."
        )
    if n >= 5 and summary.p90_wall > wall * 1.5:
        out.append(
            f"High variance: p90 wall time is {fmt_dur(summary.p90_wall)} against a "
            f"{fmt_dur(wall)} median. Look at the p90 column for the jobs behind the tail."
        )
    if summary.loose_jobs:
        out.append(
            "Could not link "
            + ", ".join(f"'{j}'" for j in summary.loose_jobs[:3])
            + (" and others" if len(summary.loose_jobs) > 3 else "")
            + " to a dependency by timing; their position on the path is approximate."
        )
    return out
