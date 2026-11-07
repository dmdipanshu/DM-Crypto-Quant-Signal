# Resource Budget

Target hardware: a Windows development machine with roughly 8 GB of physical
RAM. `AGENTS.md` mandates a **4 GB** envelope, and that is the **hard budget
for SignalForge's own processing workload** — not a limit on the operating
system.

The remaining memory is deliberate headroom for Windows, the browser,
development tools, background processes and other user workloads. The design
must assume it may never use more than 4 GB, and must degrade with an explicit
error rather than by competing for memory it does not own.

## 1. Budget summary

| Resource | Budget | Hard ceiling | Enforcement |
|---|---|---|---|
| Process count (baseline) | 1 | 1 | CLI runs one job; `sf serve` is one process |
| Resident memory, research job | 1.2 GB | 2.0 GB | Explicit row caps, chunked reads |
| Resident memory, server | 250 MB | 500 MB | Bounded pagination and scans |
| Peak transient during ingestion | 400 MB | 800 MB | Page caps, streamed writes |
| Peak transient during training | 1.5 GB | 2.5 GB | `max_rows_train`, `n_jobs=1`, chunking |
| CPU threads | 1 | 2 | `n_jobs=1`; no thread pools for data work |
| Disk per snapshot (1 year, 1h) | ~20 MB | ~50 MB | Parquet compression |
| Open file handles | < 32 | 64 | Context-managed reads |
| AI requests in flight | 1 | 1 | External inference; bounded response size |
| Startup time (CLI) | < 2 s | 5 s | Lazy optional imports |

These are design limits, not measured claims. Any published number must come
from an actually-run measurement recorded with its command.

## 2. Why the 4 GB figure wins

| Source | Figure | Consequence |
|---|---|---|
| `AGENTS.md` §2 | 4 GB mandatory | Existing operating constraint |
| Host machine | ~8 GB physical RAM | Hardware capacity, not an allocation |
| Decision | 4 GB hard budget | SignalForge's workload never exceeds it |

The 4 GB figure is a **budget for this application**, not a statement about the
machine. It does not mean the operating system runs in 4 GB, and it does not
authorize SignalForge to consume whatever is free. The remaining ~4 GB belongs
to Windows, the browser, tooling and other work, and is treated as unavailable
headroom.

Consequences:

- Designing to 4 GB is strictly safer than designing to 8 GB. If a workload
  fits in 4 GB it fits in 8 GB; the reverse is false.
- Heavy AI inference is external by design (`AI_GATEWAY.md` §13); the laptop
  orchestrates and normalizes, it does not host a general model.
- A future request to raise caps requires a measured run and a documented
  decision, never a silent change.

This supersedes the earlier "4 GB vs 8 GB conflict" open item: the question is
resolved, and the stricter value governs.

## 3. Per-workload bounds

| Workload | Bound | Mechanism | On exceed |
|---|---|---|---|
| Ingestion | `max_pages`, `page_limit`, `max_candles_per_request` | Page loop with flush | Abort with explicit error |
| Snapshot load | `max_rows_load` | Column projection, row-group reads | Refuse, suggest slicing |
| Feature build | `chunk_rows` | Chunked transform, concat at the end | Refuse |
| Training | `max_rows_train` (default 2,000) | Downsample with a recorded warning | Refuse if configured cap is 0 |
| Backtest | `max_bars` (default 5,000) | Explicit guard | Refuse |
| Walk-forward | `n_folds`, `train_window`, `val_window` | Fold loop, one fold in memory | Refuse |
| Bootstrap | `max_bootstrap` (default 200) | Bounded resampling | Refuse |
| API list | `page_size_max` (500) | Enforced pagination | 422 |
| API ledger scan | `ledger_scan_cap` (5,000 lines) | Bounded scan | Truncated with a flag |
| Dashboard | Fixed page size | No unbounded query | N/A |
| AI | 1 request in flight; bounded request, response, token, timeout and retry caps (see below) | Enforced in `ai/gateway.py`; `[ai.budgets]` is reserved and inert | Explicit error / bounded retry |

**Current implementation (Stage 0-5, 2026-11-07).** The gateway enforces:
concurrency exactly 1, `max_request_chars` 8,000, `max_response_chars` 4,000,
`max_output_tokens` 1,024, `timeout_s` 30, retries 0-3 (default 2, exponential
backoff capped at 2 s), adapter response read cap 200,000 bytes, and evidence
capped at 64 keys and 8,000 chars per value. `sf ai test` is offline/mock by
default; `--live` requires explicit configuration.

**Reserved / not implemented.** `[ai.budgets]` (`max_requests`, `max_chars`) is
parsed and validated but **inert**: the Stage 0-5 gateway does not enforce a
runtime budget tracker, and no paid fallback or runtime provider fallback is
wired. `[ai].default_provider` and `fallback_order` are legacy fields that do
not route requests; routing is `[ai.tasks.<task>].route` only. The legacy
`BudgetTracker` / `run_with_fallback` code in `ai_providers/` belongs to the
Phase 4 path and is not imported by `crypto_engine.ai`; it must not be
described as part of the new gateway. Wiring `[ai.budgets]` or any other
parsed-but-unenforced field requires separate authorization and review (M2);
see `AI_GATEWAY.md` §16.9.

**Design intent (not implemented).** The earlier design described an in-memory
budget tracker that would count requests/characters and stop before a paid
fallback provider. That intent is not wired in the Stage 0-5 gateway and must
not be read as current behaviour.

## 4. Data handling rules

1. **Project columns at read time.** Never read a full Parquet file into a
   wide frame when only a subset is needed.
2. **Read row groups.** Prefer bounded row-group iteration for large files.
3. **Avoid copies.** Use in-place operations where safe; do not keep both raw
   and normalized frames alive longer than necessary.
4. **One symbol and one timeframe at a time** by default. Batch loops flush
   before the next item.
5. **No full-matrix copies** for feature scaling; scale in place or on the
   column slice.
6. **Free aggressively.** Delete intermediate frames and call garbage
   collection at chunk boundaries when a long loop is expected.
7. **Never silently drop rows.** If a cap applies, record it in the manifest
   or report.

## 5. Process model

| Mode | Processes | Threads | Notes |
|---|---|---|---|
| CLI research | 1 | 1 | One job at a time |
| Ingestion | 1 | 1 | One worker, sequential symbols |
| Training | 1 | 1 | `n_jobs=1` everywhere |
| Backtest | 1 | 1 | One symbol/timeframe |
| `sf serve` | 1 | small fixed pool | Uvicorn default workers = 1 |
| Scheduler | shares the CLI process | 1 | No daemon |
| AI calls | 1 in flight | 1 | Sequential fallback |

No multiprocessing pool, no job queue, no worker fleet.

## 6. Memory safeguards

- Configuration is validated at load; caps cannot be negative or zero unless
  explicitly meaning "unlimited", which is not offered.
- Every bounded operation emits a structured warning when it hits its cap.
- `MemoryError` is caught at use-case boundaries and converted to an
  actionable error naming the limit and the suggested remediation.
- The system never retries a failed large operation at a larger size.
- The system never streams an unbounded generator into a list.

## 7. Performance measurement points

Instrumentation is required at these points, with timings recorded in reports:

| Point | Metric | Purpose |
|---|---|---|
| Fetch page | latency, bytes, rows | Rate-limit tuning |
| Normalize/validate | rows per second | Bottleneck detection |
| Snapshot write | bytes, duration | Disk behaviour |
| Snapshot load | rows, duration | Read path cost |
| Feature build | rows, duration, peak alloc | Hot path |
| Strategy evaluation | per-strategy duration | Ablation and profiling |
| Scoring | duration | Determinism check |
| Backtest | bars per second | Research throughput |
| Walk-forward | fold duration | Planning |
| API request | duration, response bytes | UX |
| Dashboard render | duration, HTML bytes | UX |
| Report write | duration | I/O |

Measurements use `time.perf_counter` and, where available, `tracemalloc` for
Python-allocated peaks. Process RSS may be reported by the operator but is not
assumed to be available in tests.

## 8. Startup and dependency discipline

- Baseline import graph must not import `fastapi`, `uvicorn`, `ccxt` adapters
  or AI SDKs until needed.
- Optional extras are imported lazily inside the adapter or command.
- A missing optional dependency produces a message naming the exact install
  command; it never crashes the CLI at import time.
- The core test suite runs without network and without optional extras.

## 9. Test resource limits

| Test class | Bound |
|---|---|
| Unit | ≤ 400 rows per fixture |
| Integration | ≤ 2,000 rows |
| Leakage | Small synthetic series |
| Architecture | Static AST only, no imports |
| API/dashboard | Fixture files, no live server except one smoke test |

Skips are never counted as passes. A skipped optional test is reported as a
skip with its reason.

## 10. Degradation policy

| Condition | Behaviour |
|---|---|
| Memory pressure detected | Abort the operation with the limit name and remediation |
| Disk full | Abort the write; the partial temp directory is removed |
| Rate limit exhausted | Abort ingestion; no partial snapshot |
| CPU starvation | No automatic parallelism; the job simply takes longer |
| Clock skew | Halt time-sensitive operations |

The system never trades correctness for memory. It stops with an error.

## 11. Resolution record

Resolved 2026-11-07 by the operator's architecture brief:

- the host has roughly 8 GB of physical RAM;
- **4 GB is the hard budget for SignalForge's own processing**, not an OS
  limit;
- the remaining memory is headroom for Windows, browser, tooling and other
  workloads;
- the 4 GB envelope governs, and all caps in §3 remain design limits.

No cap is raised by this resolution. Raising one still requires an
actually-run measurement and a recorded decision. The roadmap no longer treats
the 4 GB vs 8 GB question as a blocking stop condition.

## 12. AI and the envelope

AI inference is external (`AI_GATEWAY.md` §13). The laptop's AI cost is:

| Item | Bound |
|---|---|
| Concurrent requests | 1 |
| Request payload | ≤ 8,000 chars per value, ≤ 64 keys |
| Response payload | Bounded; never held beyond normalization |
| Local model weights | None in the baseline |
| Local inference | Not the baseline; optional adapter only, disabled by default |

An optional local-inference adapter must declare its memory footprint and must
not run concurrently with training, backtests or large research jobs. The
current `ollama` adapter is recorded as an existing exception, not a baseline
requirement.
