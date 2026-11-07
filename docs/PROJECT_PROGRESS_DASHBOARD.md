# Project Progress & Development Control Dashboard

Read-only control center for the complete SignalForge development lifecycle.
It is served by the existing local API/dashboard process and reads only the
append-only progress ledger under `data/project_progress/`.

## URL and launch

```powershell
uv sync --extra api
uv run sf serve
# open http://127.0.0.1:8000/dashboard/progress/
```

`sf serve` is loopback-only by default. The progress page is also linked from
the research dashboard navigation as **Progress**.

The page is plain HTML5, CSS3 and vanilla JavaScript. There is no framework,
no npm, no build step, no external network request and no external analytics.

## What it shows

| Section | Source |
|---|---|
| Overview | Derived ledger metrics plus the static recorded project model |
| Project Roadmap | Historical Phase A-L records from `memory/` |
| Development Timeline | Recorded events plus the PROJECT-to-EVIDENCE hierarchy |
| Build Activity | Append-only event feed with filters and search |
| Phases | Phase table and expandable historical detail |
| AI Stages | Stage 0-5 recorded reports; all six are FROZEN after the supplied final acceptance audit, with live-provider NOT_VERIFIED |
| Architecture | Implemented layers and the AI advisory flow |
| Subsystems | The 20 documented components |
| Testing & Quality | Recorded test events and Stage test groups |
| Security | Deterministic checks, M2 inert fields and findings |
| Resource Monitor | Documented limits and recorded probes only |
| Issues & Risks | Findings and authorization boundaries |
| Git & Changes | Last recorded read-only Git snapshot |
| Evidence & Reports | Verification and evidence events |
| Documentation | Relevant repository documents |
| Glossary | Plain-language definitions |

## Simple and Technical views

Both views use the same data.

- **Simple** answers: what happened, why, whether it works, what is not
  verified, and what happens next. Stage and phase cards use plain language.
- **Technical** shows module paths, commands, test counts, error codes,
  evidence, configuration keys, file lists and Git details.

The toggle is in the header and is keyboard accessible.

## Live refresh and stale data

- The page polls the read-only endpoints every 3 seconds **only while the
  page is visible**.
- `document.visibilitychange` triggers an immediate refresh when the tab
  becomes visible again.
- New event IDs are highlighted once and announced once; old events are not
  replayed as new notifications.
- If the last recorded event is older than the freshness window (default
  3600 seconds), a stale-data banner is shown and the last valid state stays
  visible.
- If the endpoint is unreachable, the connection indicator switches to
  disconnected and the last valid state is retained with an explicit error
  banner.

## Read-only endpoints

All endpoints are GET-only and return the standard API envelope.

```text
GET /api/v1/progress/snapshot
GET /api/v1/progress/events?limit=50&offset=0&level=&status=&phase=&stage=&workstream=&since=&until=&q=
GET /api/v1/progress/history?limit=200&offset=0
```

`snapshot` returns the derived manifest, the five most recent events, the
staleness flags and the static project model. The dashboard never executes
Git, tests, training, AI providers or trading from a request.

## Historical versus current evidence

- Phases A-L are labelled `HISTORICAL`. They are recorded project context,
  not current filesystem evidence. Deleted Phase L artifacts are never shown
  as present.
- All six AI stages (0-5) are labelled `FROZEN` with the acceptance date
  `2026-11-07` and the verdict `AI FOUNDATION — ACCEPTED / FROZEN`. The
  evidence is the supplied Stage 0-5 final acceptance audit; it is recorded
  evidence, not a fresh execution.
- Test totals stay separate and are never combined: the Stage 0-5 audit
  recorded 785 passed / 4 skipped / 0 failed, the Stage 0-5 focused suite
  recorded 242 passed, the Project Progress Dashboard integration recorded
  831 passed / 4 skipped, and the later freeze-dashboard update recorded
  836 passed / 4 skipped. The AI view shows the first two; the ledger and
  Overview show the dashboard runs with their own dates and scope.
- M2 residual configuration fields are shown as `INERT` and the classification
  is `M2 ACCEPTED AS DOCUMENTED`. Unsupported header/query auth mechanisms
  fail closed; only bearer and loopback-none authentication are implemented;
  no runtime M2 wiring is authorized.
- The `RESOURCE_BUDGET.md:71` stale AI Budget Tracker design row is displayed as
  `CORRECTED` (severity low, non-blocking), with the correction evidence and the
  completion event `evt-20261107T000625Z-f31f2348` at `2026-11-07T00:06:25Z`.
  The display keeps the original finding event
  `evt-20261106T234507Z-267ecb81` and its `PASS_WITH_FINDINGS` status in
  history; the later documentation and verification `PASS` events establish the
  resolution. The correction is display metadata only — the ledger status enum
  and the append-only history are unchanged.
- Live external provider connectivity is `NOT_VERIFIED`.
- Public deployment and trading readiness remain `NOT_VERIFIED` even when the
  local test suite passes.

## Security properties

- GET-only routes, asserted at application build time.
- Content-Security-Policy: `default-src 'self'`, no inline scripts, no
  inline event handlers.
- All dynamic text is inserted with `textContent` / DOM construction; no
  dynamic `innerHTML`.
- No absolute filesystem paths, secrets, prompts or credentials are served.
- The page is loopback-only unless the operator explicitly changes the bind
  configuration. Local tests do not approve public exposure.
- No trading controls, order forms, mutation endpoints or write paths.

## Troubleshooting

| Symptom | Check |
|---|---|
| Page says the ledger is not initialized | Run `uv run python scripts/project_progress.py seed` |
| Stale-data banner | The last event is older than the freshness window; record a current update |
| Disconnected indicator | Confirm `sf serve` is running and `/api/v1/progress/snapshot` responds |
| Corrupt-record warning | Run `uv run python scripts/project_progress.py validate` and inspect `data/project_progress/events.jsonl` line numbers |
| Git section empty | Run `uv run python scripts/project_progress.py snapshot` |
| API extra missing | Run `uv sync --extra api` (a plain `uv sync` prunes the extra and the API/dashboard tests then skip) |

## Limitations

- The dashboard displays only events that were actually written to the
  ledger. It does not capture private internal agent activity automatically.
- Browser automation is not bundled. HTTP, DOM and static asset checks are
  recorded in the test suite; a manual browser check is still useful for
  visual confirmation.
- No live RAM or GPU utilization is shown because no safe live probe is
  implemented. Documented limits and previously recorded probes are shown
  instead.
- The ledger is project data, not a production audit log. It is append-only
  by convention and by the recorder, but a user with filesystem access can
  still edit it; `validate` reports corruption rather than hiding it.
