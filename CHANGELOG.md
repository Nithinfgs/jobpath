# Changelog

## 0.1.0

First release.

- Critical path inferred from job timestamps (no workflow YAML parsing).
- Simulated "what if this job were 30% faster / removed" savings, reported as a range when
  timestamps are ambiguous.
- Time on the critical path split into queue, orchestration, runner, checkout, toolchain, cache,
  dependency, artifact and work.
- Multi-run aggregation: jobs by expected impact, slowest critical-path steps, setup tax, variance.
- Output as terminal, Markdown (with Mermaid Gantt), JSON and standalone HTML.
- `--budget` exit code for gating on median wall time; `--save` / `--file` for offline analysis;
  `--demo` bundled sample.
