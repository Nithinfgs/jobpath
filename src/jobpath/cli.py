"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from typing import List, Optional, Tuple

from . import __version__
from .analyze import DEFAULT_TOLERANCE, analyze_run, summarize
from .github import (
    Client,
    GitHubError,
    dump_raw,
    fetch_run,
    fetch_runs,
    list_workflows,
    load_raw,
    parse_target,
    repo_from_git_remote,
    resolve_token,
)
from .model import Run, parse_run
from .render_export import render_html, render_json, render_markdown
from .render_term import render_terminal


def parse_duration(text: str) -> float:
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", text.strip())
    if not m or not any(m.groups()):
        raise argparse.ArgumentTypeError(f"bad duration {text!r}; try 90s, 12m or 1h30m")
    h, mnt, s = (int(x or 0) for x in m.groups())
    return float(h * 3600 + mnt * 60 + s)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jobpath",
        description="Find the critical path of your GitHub Actions workflow: "
        "which jobs decide wall time, and what speeding each one up would save.",
    )
    p.add_argument(
        "target", nargs="?", help="owner/repo, or a workflow run URL (default: this repo's origin)"
    )
    p.add_argument("-w", "--workflow", help="workflow file name or id, e.g. ci.yml")
    p.add_argument(
        "-n", "--runs", type=int, default=10, help="how many recent successful runs to analyse (default 10)"
    )
    p.add_argument("--branch", help="only runs on this branch")
    p.add_argument("--event", help="only runs triggered by this event (push, pull_request, ...)")
    p.add_argument("--file", help="analyse a JSON file written by --save instead of calling GitHub")
    p.add_argument("--save", metavar="FILE", help="write the raw API data to FILE for offline re-analysis")
    p.add_argument("-f", "--format", choices=["term", "md", "json", "html"], default="term")
    p.add_argument("-o", "--output", metavar="FILE", help="write the report to FILE instead of stdout")
    p.add_argument("--all", action="store_true", help="list every job in the terminal timeline")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colors (NO_COLOR is also honoured)")
    p.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        metavar="SEC",
        help="max seconds between a job finishing and its dependent starting (default 5)",
    )
    p.add_argument(
        "--budget",
        type=parse_duration,
        metavar="DUR",
        help="exit 1 if median wall time exceeds DUR (e.g. 10m), for use as a CI gate",
    )
    p.add_argument("--version", action="version", version=f"jobpath {__version__}")
    return p


def _load_runs(args: argparse.Namespace) -> Tuple[List[Run], str]:
    """Return (runs, title)."""
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            runs = load_raw(f.read())
        return runs, args.file

    client = Client(resolve_token())
    repo: Optional[str]
    run_id = None
    if args.target:
        repo, run_id = parse_target(args.target)
    else:
        repo = repo_from_git_remote()
        if not repo:
            raise GitHubError("no target given and no GitHub remote found; pass owner/repo")

    if run_id:
        raw = [fetch_run(client, repo, run_id)]
        title = f"{repo} run #{run_id}"
    else:
        workflow = args.workflow
        if not workflow:
            wfs = list_workflows(client, repo)
            if len(wfs) == 1:
                workflow = str(wfs[0]["id"])
            else:
                names = "\n".join(f"  -w {w['path'].split('/')[-1]:<28} {w['name']}" for w in wfs)
                raise GitHubError(f"{repo} has several workflows; choose one:\n{names}")
        raw = fetch_runs(client, repo, workflow, args.runs, args.branch, args.event)
        title = f"{repo} · {raw[0][0].get('name', workflow)}"
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(dump_raw(raw))
    return [parse_run(r, j) for r, j in raw], title


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runs, title = _load_runs(args)
        analyses = [a for a in (analyze_run(r, args.tolerance) for r in runs) if a]
        if not analyses:
            raise GitHubError("no completed jobs found in the selected runs")
    except GitHubError as e:
        print(f"jobpath: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"jobpath: {e}", file=sys.stderr)
        return 2

    summary = summarize(analyses)
    if args.format == "md":
        text = render_markdown(summary, title)
    elif args.format == "json":
        text = render_json(summary, title)
    elif args.format == "html":
        text = render_html(summary, title)
    else:
        color = not args.no_color and sys.stdout.isatty() and not args.output and "NO_COLOR" not in os.environ
        width = min(shutil.get_terminal_size((100, 24)).columns, 120)
        text = render_terminal(summary, title, color=color, width=width, show_all=args.all)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"jobpath: wrote {args.output}", file=sys.stderr)
    else:
        print(text)

    if args.budget is not None and summary.median_wall > args.budget:
        print(
            f"jobpath: median wall time exceeds budget ({summary.median_wall:.0f}s > {args.budget:.0f}s)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
