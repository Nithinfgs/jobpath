from __future__ import annotations

import pytest

from conftest import diamond, job, run
from jobpath.analyze import (
    analyze_run,
    classify_step,
    fmt_dur,
    fmt_range,
    insights,
    normalize_step,
    summarize,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Set up job", "runner"),
        ("Complete job", "runner"),
        ("Post Run actions/checkout@v4", "checkout"),
        ("Run actions/checkout@3d3c42e", "checkout"),
        ("Run actions/setup-node@v4", "toolchain"),
        ("Run astral-sh/setup-uv@v6", "toolchain"),
        ("Run npm ci", "deps"),
        ("Run pip install -r requirements.txt", "deps"),
        ("Restore cargo cache", "cache"),
        ("Post Run actions/cache@v4", "cache"),
        ("Run actions/upload-artifact@v4", "artifacts"),
        ("Run npm test", "work"),
    ],
)
def test_classify_step(name: str, expected: str) -> None:
    assert classify_step(name) == expected


def test_normalize_step_strips_pins() -> None:
    assert normalize_step("Run actions/checkout@3d3c42e5aac5") == "Run actions/checkout"
    assert normalize_step("Run actions/setup-node@v4.1.0") == "Run actions/setup-node"


def test_critical_path_follows_longest_chain() -> None:
    a = analyze_run(diamond())
    assert a is not None
    assert [n.job.name for n in a.path] == ["lint", "build", "test", "deploy"]
    assert a.wall == 120


def test_path_time_adds_up_to_wall_time() -> None:
    a = analyze_run(diamond())
    assert a is not None
    assert sum(n.latency + n.duration for n in a.path) == pytest.approx(a.wall)
    assert sum(a.path_categories.values()) == pytest.approx(a.wall)


def test_dependencies_are_inferred_from_timestamps() -> None:
    a = analyze_run(diamond())
    assert a is not None
    by_name = {j.name: j for j in a.run.jobs}
    assert [p.name for p in a.preds[by_name["build"].id]] == ["lint"]
    assert {p.name for p in a.preds[by_name["deploy"].id]} == {"test"}
    assert a.preds[by_name["docs"].id] == []


def test_speedup_is_capped_by_parallel_branch() -> None:
    # Two independent branches: 100s and 90s. Removing the long one saves only 10s.
    r = run([job("slow", 0, 1, 100), job("fast", 0, 1, 90)])
    a = analyze_run(r)
    assert a is not None
    g = a.gains[a.path[0].job.id]
    assert g.removed == pytest.approx(10)
    assert g.faster_30 == pytest.approx(10)  # 30% of 99s = 29.7s, but fast branch ends at 90


def test_speedup_on_serial_chain_is_proportional() -> None:
    r = run([job("a", 0, 0, 10), job("b", 10, 10, 110)])
    a = analyze_run(r)
    assert a is not None
    b = next(n for n in a.path if n.job.name == "b")
    assert a.gains[b.job.id].faster_30 == pytest.approx(30)
    assert a.gains[b.job.id].removed == pytest.approx(100)


def test_simultaneous_finishers_produce_a_range() -> None:
    # `x` and `y` both end at t=50; `z` starts right after. Either could gate it.
    r = run([job("x", 0, 0, 50), job("y", 0, 0, 50.5), job("z", 51, 51, 100)])
    a = analyze_run(r)
    assert a is not None
    x = next(j for j in a.run.jobs if j.name == "x")
    y = next(j for j in a.run.jobs if j.name == "y")
    assert {p.name for p in a.preds[next(j for j in a.run.jobs if j.name == "z").id]} == {"x", "y"}
    # y finishes last so it is on the path; speeding it up helps only until x (50s) gates z.
    g = a.gains[y.id]
    assert g.faster_30_max > g.faster_30 >= 0
    assert x.id not in a.gains


def test_skipped_and_unstarted_jobs_are_ignored() -> None:
    from jobpath.model import parse_run

    raw = [
        {
            "id": 1,
            "name": "ok",
            "created_at": "2026-01-01T12:00:00Z",
            "started_at": "2026-01-01T12:00:01Z",
            "completed_at": "2026-01-01T12:00:11Z",
            "conclusion": "success",
        },
        {
            "id": 2,
            "name": "skipped",
            "created_at": "2026-01-01T12:00:00Z",
            "started_at": "2026-01-01T12:00:00Z",
            "completed_at": "2026-01-01T12:00:00Z",
            "conclusion": "skipped",
        },
        {
            "id": 3,
            "name": "pending",
            "created_at": "2026-01-01T12:00:00Z",
            "started_at": None,
            "completed_at": None,
            "conclusion": None,
        },
    ]
    assert [j.name for j in parse_run({"id": 9}, raw).jobs] == ["ok"]


def test_empty_run_returns_none() -> None:
    assert analyze_run(run([])) is None


def test_queue_time_is_separated_from_run_time() -> None:
    r = run([job("only", 0, 40, 100)])
    a = analyze_run(r)
    assert a is not None
    assert a.path_categories["queue"] == pytest.approx(40)
    assert a.path[0].queue == 40


def test_step_categories_are_attributed() -> None:
    a = analyze_run(diamond())
    assert a is not None
    test_node = next(n for n in a.path if n.job.name == "test")
    assert test_node.categories["deps"] == 15
    assert test_node.categories["toolchain"] == 6
    assert test_node.categories["work"] == 30
    assert test_node.categories["checkout"] == 5 + 1


def test_summary_and_insights_over_several_runs() -> None:
    runs = [diamond() for _ in range(5)]
    s = summarize([a for a in (analyze_run(r) for r in runs) if a])
    assert s.median_wall == 120
    assert s.jobs[0].name == "test"  # biggest expected saving
    assert s.jobs[0].critical == 5
    text = " ".join(insights(s))
    assert "Long pole: 'test'" in text


def test_fmt_helpers() -> None:
    assert fmt_dur(45) == "45s"
    assert fmt_dur(72) == "1m12s"
    assert fmt_dur(3720) == "1h02m"
    assert fmt_range(0, 1) == "1s"
    assert fmt_range(2, 40) == "2s–40s"
