# Contributing

Thanks for helping. jobpath is small on purpose: stdlib only, one clear job.

## Setup

```bash
git clone https://github.com/Nithinfgs/jobpath && cd jobpath
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check . && ruff format --check . && mypy
jobpath --demo
```

## The most useful contribution: a recorded run

The analysis is only as good as the timestamp patterns it has seen. If jobpath draws a wrong critical path
for your workflow, run `jobpath owner/repo -w ci.yml --save runs.json` (public repos only), open an issue,
and say which job you expected. A trimmed run makes an excellent regression test in `tests/`.

## Guidelines

- No runtime dependencies. If you need one, open an issue first.
- Keep claims honest: savings are simulations, and the output must say so.
- New findings (in `analyze.insights`) need a threshold that avoids noise on small workflows, and a test.
- Style is enforced by `ruff` and `mypy --strict`; CI runs on Python 3.9 to 3.13, Linux, macOS and Windows.

## Ideas that are welcome

See the roadmap in the README.
