from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from jobpath.cli import main, parse_duration
from jobpath.github import GitHubError, dump_raw, load_raw, parse_target

FIXTURE = str(Path(__file__).parent.parent / "examples" / "ruff-ci.json")


def test_parse_target() -> None:
    assert parse_target("astral-sh/ruff") == ("astral-sh/ruff", None)
    assert parse_target("https://github.com/astral-sh/ruff") == ("astral-sh/ruff", None)
    assert parse_target("https://github.com/astral-sh/ruff/actions/runs/12345/job/678") == (
        "astral-sh/ruff",
        12345,
    )
    with pytest.raises(GitHubError):
        parse_target("not a repo")


def test_parse_duration() -> None:
    assert parse_duration("90s") == 90
    assert parse_duration("12m") == 720
    assert parse_duration("1h30m") == 5400
    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration("soon")


def test_load_raw_roundtrip_and_single_jobs_response() -> None:
    raw = json.loads(Path(FIXTURE).read_text())
    runs = load_raw(json.dumps(raw))
    assert len(runs) == 4
    assert dump_raw([(r["run"], r["jobs"]) for r in raw["runs"]])
    single = {"jobs": raw["runs"][0]["jobs"]}
    assert len(load_raw(json.dumps(single))) == 1
    with pytest.raises(GitHubError):
        load_raw("[]")
    with pytest.raises(GitHubError):
        load_raw("{nope")


def test_terminal_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--file", FIXTURE, "--no-color"]) == 0
    out = capsys.readouterr().out
    for section in (
        "TIMELINE",
        "CRITICAL PATH",
        "WHERE THE CRITICAL PATH GOES",
        "JOBS BY IMPACT",
        "FINDINGS",
    ):
        assert section in out
    assert "Determine changes" in out
    assert "\033[" not in out


@pytest.mark.parametrize("fmt,needle", [("md", "```mermaid"), ("html", "<svg"), ("json", '"critical_path"')])
def test_export_formats(fmt: str, needle: str, tmp_path: Path) -> None:
    out = tmp_path / f"report.{fmt}"
    assert main(["--file", FIXTURE, "-f", fmt, "-o", str(out)]) == 0
    assert needle in out.read_text()


def test_json_is_valid(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--file", FIXTURE, "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["runs"] == 4
    assert data["typical_run"]["critical_path"][0]["job"] == "Determine changes"


def test_budget_gate(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--file", FIXTURE, "--no-color", "--budget", "2h"]) == 0
    assert main(["--file", FIXTURE, "--no-color", "--budget", "1m"]) == 1
    assert "exceeds budget" in capsys.readouterr().err


def test_missing_file_is_a_clean_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--file", "/nonexistent/x.json"]) == 2
    assert "jobpath:" in capsys.readouterr().err
