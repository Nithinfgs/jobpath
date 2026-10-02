"""Terminal report: timeline, critical path, job impact table, findings."""

from __future__ import annotations

import textwrap
from typing import Dict, List

from .analyze import RunAnalysis, Summary, fmt_dur, fmt_range, insights

_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36", "grey": "90"}


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        codes = ";".join(_CODES[s] for s in styles)
        return f"\033[{codes}m{text}\033[0m"


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _timeline(a: RunAnalysis, st: Style, width: int, rows: int) -> List[str]:
    jobs = sorted(a.run.jobs, key=lambda j: j.started)
    critical = {n.job.id for n in a.path}
    shown = [j for j in jobs if j.id in critical]
    rest = sorted((j for j in jobs if j.id not in critical), key=lambda j: -j.duration)
    shown += rest[: max(0, rows - len(shown))]
    hidden = len(jobs) - len(shown)
    shown.sort(key=lambda j: j.started)

    label_w = min(38, max(len(j.name) for j in shown))
    bar_w = max(20, width - label_w - 12)
    scale = bar_w / a.wall if a.wall else 0

    lines = []
    for j in shown:
        q0 = int(a.offset(j.created) * scale)
        s0 = int(a.offset(j.started) * scale)
        e0 = max(s0 + 1, int(round(a.offset(j.completed) * scale)))
        is_crit = j.id in critical
        queue = "░" * max(0, s0 - q0)
        body = "█" * (e0 - s0)
        bar = " " * q0 + st(queue, "grey") + st(body, "red" if is_crit else "cyan")
        pad = " " * max(0, bar_w - e0)
        name = _clip(j.name, label_w).ljust(label_w)
        name = st(name, "bold") if is_crit else st(name, "dim")
        lines.append(f"  {name} {bar}{pad} {fmt_dur(j.duration):>6}")
    if hidden:
        lines.append(st(f"  … {hidden} shorter jobs not shown (--all to list every job)", "grey"))
    return lines


def _category_bars(cats: Dict[str, float], wall: float, st: Style) -> List[str]:
    rows = [(c, v) for c, v in cats.items() if v >= 0.5]
    rows.sort(key=lambda kv: -kv[1])
    out = []
    for c, v in rows:
        share = v / wall if wall else 0
        bar = "█" * max(1, int(round(share * 30)))
        color = "red" if c == "work" else "yellow" if c in ("queue", "orchestration") else "cyan"
        out.append(f"  {c:<14}{st(bar.ljust(30), color)} {fmt_dur(v):>6} {share:>4.0%}")
    return out


def render_terminal(
    summary: Summary,
    title: str,
    color: bool = True,
    width: int = 100,
    top: int = 18,
    show_all: bool = False,
) -> str:
    st = Style(color)
    a = summary.typical
    n = len(summary.analyses)
    out: List[str] = []

    out.append(st("jobpath", "bold", "red") + "  " + st(title, "bold"))
    stats = (
        f"median wall {st(fmt_dur(summary.median_wall), 'bold')}"
        + (f" (p90 {fmt_dur(summary.p90_wall)})" if n > 1 else "")
        + f" · job time {fmt_dur(summary.median_job_seconds)}"
        + f" · parallelism {summary.median_parallelism:.1f}x"
        + f" · {n} run{'s' if n != 1 else ''}"
    )
    out.append(stats)
    out.append("")

    label = "run" if n == 1 else "typical run"
    out.append(
        st(f"TIMELINE  {label} #{a.run.id} ({fmt_dur(a.wall)})", "bold")
        + st("   ", "dim")
        + st("█", "red")
        + " critical path  "
        + st("█", "cyan")
        + " other  "
        + st("░", "grey")
        + " queued"
    )
    out += _timeline(a, st, width, 10_000 if show_all else top)
    out.append("")

    out.append(st("CRITICAL PATH", "bold") + st("  (jobs that decided wall time; saving = simulated)", "dim"))
    for i, node in enumerate(a.path, 1):
        g = a.gains[node.job.id]
        work = max(
            (s for s in node.job.steps),
            key=lambda s: s.duration if s.name not in ("Set up job", "Complete job") else -1,
            default=None,
        )
        slow = (
            f"  slowest step: {_clip(work.name, 42)} {fmt_dur(work.duration)}"
            if work and work.duration >= 5
            else ""
        )
        wait = f"wait {fmt_dur(node.latency)}" if node.latency >= 1 else ""
        saving = (
            st("−30% saves " + fmt_range(g.faster_30, g.faster_30_max), "green")
            if g.faster_30_max >= 1
            else st("no gain alone: a parallel branch ties", "yellow")
        )
        name = _clip(node.job.name, 34).ljust(34)
        out.append(
            f"  {i}. {st(name, 'bold')} run {fmt_dur(node.duration):>6}  {st(wait.ljust(8), 'yellow')} {saving}"
        )
        if slow:
            out.append(st("     " + slow.strip(), "grey"))
    out.append("")

    out.append(st("WHERE THE CRITICAL PATH GOES", "bold"))
    out += _category_bars(summary.path_categories, summary.median_wall, st)
    out.append("")

    if n > 1:
        out.append(st(f"JOBS BY IMPACT  (over {n} runs)", "bold"))
        out.append(st(f"  {'job':<34}{'on path':>8}{'median':>8}{'p90':>8}{'−30% saves':>12}", "dim"))
        for js in summary.jobs[:8]:
            if not js.critical:
                break
            out.append(
                f"  {_clip(js.name, 33):<34}{f'{js.critical}/{n}':>8}{fmt_dur(js.median_duration):>8}"
                f"{fmt_dur(js.p90_duration):>8}{fmt_range(js.median_gain_30, js.median_gain_30_max):>12}"
            )
        out.append("")

    steps = [s for s in summary.steps if s.category == "work" and s.median_duration >= 5][:5]
    if steps:
        out.append(st("SLOWEST STEPS ON THE CRITICAL PATH", "bold"))
        for s in steps:
            out.append(f"  {fmt_dur(s.median_duration):>6}  {_clip(s.job, 28)} › {_clip(s.step, 44)}")
        out.append("")

    found = insights(summary)
    if found:
        out.append(st("FINDINGS", "bold"))
        for f in found:
            wrapped = textwrap.wrap(f, width=max(40, width - 4))
            out.append("  • " + wrapped[0])
            out += ["    " + w for w in wrapped[1:]]
        out.append("")
    out.append(
        st(
            "Critical path is inferred from job timestamps; savings are a simulation, not a measurement.",
            "grey",
        )
    )
    return "\n".join(out)
