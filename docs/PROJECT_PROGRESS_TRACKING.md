# Project Progress Tracking

The progress ledger is the single source of truth for the Project Progress &
Development Control dashboard. It is append-only JSONL plus a derived,
atomically replaced manifest.

```text
data/project_progress/
    events.jsonl     # append-only source of truth
    manifest.json    # derived state (rebuilt atomically)
    schema.json      # machine-readable schema and status transitions
```

## Recording an update

Use the standard-library utility. It validates the event, enforces status
transitions and refreshes the manifest atomically.

```powershell
uv run python scripts/project_progress.py record `
  --level task `
  --workstream progress_dashboard `
  --phase J `
  --task-id progress_dashboard `
  --status IN_PROGRESS `
  --title "Frontend implementation started" `
  --summary "The read-only dashboard frontend implementation started." `
  --files src/crypto_engine/dashboard/progress/index.html `
  --next-action "Run the focused tests." `
  --agent quant-lead
```

Common operations:

```powershell
uv run python scripts/project_progress.py seed       # recorded historical seed events
uv run python scripts/project_progress.py list       # newest first
uv run python scripts/project_progress.py validate   # corrupt lines, duplicates, transitions
uv run python scripts/project_progress.py snapshot   # refresh manifest + read-only Git state
```

`record` options include `--details`, `--evidence`, `--blocker`,
`--tests-passed/--tests-failed/--tests-skipped/--tests-total`,
`--tests-command`, `--tests-report`, `--simple-what/--simple-why/
--simple-working/--simple-not-verified/--simple-next`,
`--module-path`, `--function`, `--config-key`, `--error-code`, `--command`,
`--event-id`, `--timestamp` and `--force`.

The ledger root defaults to `data/project_progress` and is confined to the
repository. Absolute paths and `..` traversal are rejected. Secret-like keys
and credential-shaped values are rejected.

## Event schema (version 1)

```json
{
  "schema_version": 1,
  "event_id": "unique-id",
  "timestamp_utc": "2026-11-06T23:00:00Z",
  "level": "task",
  "workstream": "progress_dashboard",
  "phase": "J",
  "stage": null,
  "task_id": "progress_dashboard",
  "status": "IN_PROGRESS",
  "title": "Short event title",
  "summary": "Plain-language summary.",
  "details": "Longer technical explanation.",
  "files": ["src/crypto_engine/progress/ledger.py"],
  "tests": {
    "passed": 49,
    "failed": 0,
    "skipped": 0,
    "total": 49,
    "command": "uv run --offline pytest tests/unit/test_progress_*.py",
    "report": "focused verification"
  },
  "evidence": ["Recorded terminal output"],
  "blockers": [],
  "next_action": "Next authorized action.",
  "agent": "qa-release-engineer",
  "simple": {
    "what": "What happened.",
    "why": "Why it matters.",
    "working": "What is verified.",
    "not_verified": "What is not verified.",
    "next": "What happens next."
  },
  "technical": {
    "module_paths": ["src/crypto_engine/progress/"],
    "functions": ["ProgressLedger.append"],
    "config_keys": ["api.progress_dir"],
    "error_codes": ["invalid_transition"],
    "commands": ["uv run --offline pytest"]
  }
}
```

Rules:

- `schema_version`, `event_id`, `timestamp_utc`, `level`, `workstream`,
  `status`, `title` and `summary` are required.
- `timestamp_utc` must be ISO-8601 UTC (`Z` or `+00:00`); local offsets and
  naive timestamps are rejected.
- `event_id` is unique. Duplicate IDs are rejected unless the seeder is
  skipping already-present seed events.
- `files` must be repository-relative.
- `tests` counts must be non-negative integers; `total` cannot be smaller
  than passed + failed + skipped.
- Unknown top-level, `simple` or `technical` fields are rejected.
- Secret-like field names and credential-shaped values are rejected.

## Levels

`project`, `workstream`, `phase`, `stage`, `subsystem`, `task`, `step`,
`verification`, `evidence`, plus the feed categories `test`, `security`,
`file`, `error` and `agent`.

## Statuses

| Status | Meaning |
|---|---|
| `NOT_STARTED` | No work and no plan recorded yet |
| `PLANNED` | Scheduled, implementation not started |
| `IN_PROGRESS` | Actively being implemented or investigated |
| `REVIEW` | Implementation exists and is being reviewed |
| `BLOCKED` | Cannot proceed until a blocker is removed |
| `PASS` | Recorded evidence supports the result |
| `PASS_WITH_FINDINGS` | Passed with documented non-blocking findings |
| `FAILED` | Recorded evidence contradicts the result |
| `FROZEN` | Accepted and frozen; reopening needs explicit authorization |
| `HISTORICAL` | Historical context, not current filesystem evidence |
| `DEFERRED` | Intentionally postponed |
| `NOT_VERIFIED` | No recorded evidence exists yet |

Transitions are checked per `workstream + phase + stage + task_id`. Examples:
`PLANNED -> IN_PROGRESS -> PASS -> FROZEN` is valid; `FROZEN -> IN_PROGRESS`
and `PLANNED -> PASS` are rejected. Use `--force` only when an explicitly
reviewed exception is required; schema validation still applies.

### Finding lifecycle (display-only)

A finding is resolved by later `PASS` events on the same task, not by rewriting
the original event. The ledger remains append-only: the original finding event
keeps its `PASS_WITH_FINDINGS` status. The project model may carry display-only
metadata on the finding (`status: "CORRECTED"`, `corrected`, `correction_event_id`,
`correction_timestamp_utc`, `original_event_id`, `original_status`, `evidence`)
so the dashboard can show the corrected state while the history stays intact.
This metadata is not a ledger status and does not add a new status enum value.

## Agent workflow: when to record

Every authorized agent must record progress at these moments:

1. When work starts.
2. When a phase or stage starts.
3. When a major task starts.
4. After each meaningful task completion.
5. After every important verification.
6. When a test run begins and when it completes.
7. When a failure or blocker appears.
8. When a finding is fixed or accepted.
9. When a stage is completed.
10. When the final project update is prepared.

Write for a non-technical reader first, then add technical evidence:

- `summary` and `simple.*` explain what happened and why it matters.
- `details`, `files`, `tests`, `evidence` and `technical.*` carry the proof.
- Never claim a test passed unless it actually ran. Record skips as skips.
- Never record a live provider call, trading action or credential.
- Label recorded historical results with their date and source.

## Recording a stage freeze (worked example, 2026-11-06 UTC)

The supplied Stage 0-5 final acceptance audit was recorded as nine appended
events. No existing event was modified.

```powershell
# One per stage, reusing the stage's task id so the transition is checked.
uv run python scripts/project_progress.py record --level stage `
  --workstream ai_gateway --stage 2 --task-id stage_2 --status FROZEN `
  --title "Stage 2 - External provider adapter FROZEN" `
  --summary "Stage 2 is recorded as FROZEN after the supplied audit." `
  --tests-passed 33 --tests-failed 0 --tests-skipped 0 --tests-total 33 `
  --evidence "Supplied Stage 0-5 final acceptance audit (2026-11-07)"

# Overall acceptance, M2 classification and the documentation finding.
uv run python scripts/project_progress.py record --level verification `
  --workstream ai_gateway --task-id stage_0_5_final_acceptance --status FROZEN `
  --title "AI FOUNDATION - ACCEPTED / FROZEN" `
  --tests-passed 785 --tests-failed 0 --tests-skipped 4 --tests-total 789 `
  --tests-command "uv run --offline pytest -q"
```

Rules confirmed by this recording:

- `PASS -> FROZEN` and `PASS_WITH_FINDINGS -> FROZEN` are the allowed freeze
  transitions; `FROZEN` is terminal. A brand-new work item (new `task_id`) may
  be recorded directly as `FROZEN`.
- Audit numbers (785 / 4, 242 focused, 120 additional) are recorded as
  *audit-time evidence* with their source and date. They are never presented
  as a fresh run and never combined with the dashboard's own totals.
- The dashboard's verification (831 passed / 4 skipped) is a separate event
  with its own task id (`progress_dashboard_full_suite`).
- The recorder stamps real UTC recording time and generates the event id; the
  audit date lives in the summary, details, evidence and tests report.
- `seed` bootstraps the historical baseline only. Audit events recorded after
  the baseline exist in the live ledger and are not replayed by `seed`.

## Append-only and corruption behavior

- `events.jsonl` lines are never rewritten by the recorder.
- `manifest.json` is derived and replaced atomically (temp file + `os.replace`).
- Corrupt lines are reported with their line number by `validate` and by the
  API `corrupt` field; they are not silently discarded.
- The API serves a derived manifest in memory if `manifest.json` is missing;
  a GET request never creates or replaces files.
- `snapshot` is the explicit CLI operation that records read-only Git state
  (`git rev-parse`, `git status --porcelain`, `git diff --stat`). It never
  stages, commits, resets, cleans, checks out or pushes.

## Status transitions and the dashboard

The dashboard shows the latest recorded status per work item and the full
event history. It does not infer a status that was never recorded. If the
ledger is empty, the page says so and points to the `seed` command.
