"""Minimal GitHub REST client (stdlib only) for workflow runs and jobs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from .model import Run, parse_run

API = "https://api.github.com"


class GitHubError(Exception):
    """A user-facing failure talking to GitHub."""


def resolve_token() -> Optional[str]:
    """GITHUB_TOKEN / GH_TOKEN, else whatever `gh auth token` reports."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    gh = shutil.which("gh")
    if gh:
        try:
            res = subprocess.run(
                [gh, "auth", "token"], capture_output=True, text=True, timeout=10, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        token = res.stdout.strip()
        return token or None
    return None


class Client:
    def __init__(self, token: Optional[str] = None, api: str = API) -> None:
        self.token = token
        self.api = api.rstrip("/")

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self.api + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "jobpath",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise GitHubError(self._explain(e, path)) from None
        except urllib.error.URLError as e:
            raise GitHubError(f"could not reach GitHub: {e.reason}") from None

    def _explain(self, e: urllib.error.HTTPError, path: str) -> str:
        if e.code == 404:
            hint = "" if self.token else " (private repos need GITHUB_TOKEN or `gh auth login`)"
            return f"not found: {path}{hint}"
        if e.code in (401, 403):
            if e.headers.get("X-RateLimit-Remaining") == "0":
                return "GitHub API rate limit reached; set GITHUB_TOKEN or run `gh auth login`"
            return f"access denied ({e.code}) for {path}; check your token's permissions"
        return f"GitHub returned HTTP {e.code} for {path}"


def parse_target(target: str) -> Tuple[str, Optional[int]]:
    """Accept `owner/repo` or a run URL; return (repo, run_id or None)."""
    m = re.search(r"github\.com[/:]([\w.\-]+/[\w.\-]+?)(?:\.git)?/actions/runs/(\d+)", target)
    if m:
        return m.group(1), int(m.group(2))
    m = re.fullmatch(r"(?:https?://github\.com/)?([\w.\-]+/[\w.\-]+?)(?:\.git)?/?", target)
    if m:
        return m.group(1), None
    raise GitHubError(f"cannot understand target {target!r}; use owner/repo or a run URL")


def repo_from_git_remote() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return None
    m = re.search(r"github\.com[/:]([\w.\-]+/[\w.\-]+?)(?:\.git)?$", out)
    return m.group(1) if m else None


def list_workflows(client: Client, repo: str) -> List[Dict[str, Any]]:
    data = client.get(f"/repos/{repo}/actions/workflows", {"per_page": 100})
    return [w for w in data.get("workflows", []) if w.get("state") == "active"]


def _jobs_for(client: Client, repo: str, run_id: int) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    page = 1
    while True:
        data = client.get(
            f"/repos/{repo}/actions/runs/{run_id}/jobs",
            {"per_page": 100, "filter": "latest", "page": page},
        )
        jobs.extend(data.get("jobs", []))
        if len(jobs) >= data.get("total_count", 0) or not data.get("jobs"):
            return jobs
        page += 1


def fetch_run(client: Client, repo: str, run_id: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    return client.get(f"/repos/{repo}/actions/runs/{run_id}"), _jobs_for(client, repo, run_id)


def fetch_runs(
    client: Client,
    repo: str,
    workflow: str,
    count: int,
    branch: Optional[str] = None,
    event: Optional[str] = None,
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    data = client.get(
        f"/repos/{repo}/actions/workflows/{urllib.parse.quote(workflow, safe='')}/runs",
        {"status": "success", "per_page": count, "branch": branch, "event": event},
    )
    runs = data.get("workflow_runs", [])[:count]
    if not runs:
        raise GitHubError(f"no successful runs found for workflow {workflow!r} in {repo}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda r: _jobs_for(client, repo, r["id"]), runs))
    return list(zip(runs, jobs))


# ------------------------------------------------------------ offline files


def dump_raw(raw_runs: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]) -> str:
    return json.dumps({"version": 1, "runs": [{"run": r, "jobs": j} for r, j in raw_runs]}, indent=1)


def load_raw(text: str) -> List[Run]:
    """Load a file written by `--save`, or a raw `/runs/{id}/jobs` API response."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise GitHubError(f"not valid JSON: {e}") from None
    if isinstance(data, dict) and "runs" in data:
        return [parse_run(item["run"], item["jobs"]) for item in data["runs"]]
    if isinstance(data, dict) and "jobs" in data:
        first = (data["jobs"] or [{}])[0]
        meta = {
            "id": first.get("run_id", 0),
            "name": first.get("workflow_name"),
            "head_branch": first.get("head_branch"),
            "head_sha": first.get("head_sha"),
        }
        return [parse_run(meta, data["jobs"])]
    raise GitHubError("unrecognised file: expected output of `jobpath --save` or a run's jobs JSON")
