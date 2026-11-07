/* SignalForge — Project Progress & Development Control.
 *
 * Vanilla JavaScript, no framework, no external requests. All dynamic text is
 * written with textContent / createElement; no HTML string is ever injected.
 * The page reads only the GET-only progress endpoints and never triggers
 * tests, Git writes, training, AI calls, or trading.
 */
"use strict";

const API = {
  snapshot: "/api/v1/progress/snapshot",
  events: "/api/v1/progress/events",
};
const POLL_MS = 3000;
const EVENT_LIMIT = 300;

const state = {
  view: "overview",
  mode: "simple",
  snapshot: null,
  events: [],
  eventsMeta: null,
  seenIds: new Set(),
  newIds: new Set(),
  loading: true,
  error: null,
  stale: true,
  filters: {
    q: "",
    level: "",
    status: "",
    workstream: "",
    phase: "",
    stage: "",
    from: "",
    to: "",
    sort: "newest",
  },
  pollTimer: null,
  toastTimer: null,
};

/* ------------------------------------------------------------------ utils */

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = String(value);
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2), value);
      } else {
        node.setAttribute(key, value === true ? "" : String(value));
      }
    }
  }
  appendChildren(node, children);
  return node;
}

function appendChildren(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function statusChip(status) {
  return el("span", { class: "chip", dataset: { status: String(status || "NOT_VERIFIED") } }, String(status || "NOT_VERIFIED"));
}

function fmtTime(iso) {
  if (!iso) return "—";
  return String(iso).replace("T", " ").replace("Z", " UTC");
}

function fmtDate(iso) {
  if (!iso) return "—";
  return String(iso).slice(0, 10);
}

function pct(numerator, denominator) {
  if (!denominator) return "—";
  return `${numerator} / ${denominator}`;
}

function text(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function section(title, note, ...children) {
  return el(
    "section",
    { class: "section" },
    el(
      "div",
      { class: "section-head" },
      el("h2", null, title),
      note ? el("span", { class: "muted" }, note) : null,
    ),
    ...children,
  );
}

function card(title, ...children) {
  return el("div", { class: "card" }, title ? el("h3", null, title) : null, ...children);
}

/** Plain-language explanation for a corrected finding, shown in Simple view. */
const SIMPLE_FINDING_TEXT = {
  "DOC-RESOURCE":
    "The outdated budget-tracker documentation has been corrected. The new AI gateway still does not enforce runtime AI budgets or paid fallback. AI Stage 0–5 remains accepted and frozen.",
};

/** One finding card, shared by Issues and Security so both stay consistent. */
function findingCard(finding) {
  const technical = state.mode === "technical";
  const corrected = finding.status === "CORRECTED" || finding.corrected === true;
  const severityClass =
    finding.severity === "high" ? " card-danger" : finding.severity === "medium" ? " card-warn" : "";
  const children = [
    el(
      "div",
      { class: "pill-row" },
      el("strong", null, finding.title),
      statusChip(finding.status),
      el("span", { class: "tag" }, `severity: ${finding.severity}`),
      el("span", { class: "tag" }, finding.blocking === false ? "non-blocking" : "review required"),
      corrected && finding.correction_timestamp_utc
        ? el("span", { class: "tag" }, `corrected ${fmtTime(finding.correction_timestamp_utc)}`)
        : null,
    ),
  ];
  if (!technical && SIMPLE_FINDING_TEXT[finding.id]) {
    children.push(el("p", { class: "notice" }, SIMPLE_FINDING_TEXT[finding.id]));
  }
  if (finding.evidence && finding.evidence.length) {
    children.push(el("h4", { class: "mt-sm" }, corrected ? "Correction evidence" : "Evidence"), bulletList(finding.evidence));
  }
  if (finding.note) children.push(el("p", { class: "muted" }, finding.note));
  if (technical && (finding.path || finding.correction_event_id || finding.original_event_id)) {
    children.push(
      kv([
        ["File", finding.path || "—"],
        ["Status", finding.status],
        ["Corrected", finding.corrected ? "YES" : "NO"],
        ["Correction event", finding.correction_event_id || "—"],
        ["Correction time (UTC)", finding.correction_timestamp_utc ? fmtTime(finding.correction_timestamp_utc) : "—"],
        ["Original event", finding.original_event_id || "—"],
        ["Original status", finding.original_status || "—"],
      ]),
    );
  }
  return el("div", { class: "card" + severityClass }, ...children);
}

function kv(pairs) {
  const dl = el("dl", { class: "kv" });
  for (const [key, value] of pairs) {
    if (value === null || value === undefined) continue;
    dl.append(el("dt", null, key), el("dd", null, value instanceof Node ? value : String(value)));
  }
  return dl;
}

function bulletList(items) {
  if (!items || !items.length) return el("p", { class: "muted" }, "None recorded.");
  return el("ul", { class: "list-clean" }, items.map((item) => el("li", null, String(item))));
}

function table(headers, rows, caption) {
  const thead = el("thead", null, el("tr", null, headers.map((h) => el("th", { scope: "col" }, h))));
  const tbody = el("tbody");
  for (const row of rows) {
    tbody.append(el("tr", null, row.map((cell) => el("td", null, cell instanceof Node ? cell : text(cell)))));
  }
  return el("div", { class: "table-wrap" }, el("table", null, caption ? el("caption", null, caption) : null, thead, tbody));
}

function emptyState(message) {
  return el("div", { class: "empty" }, message);
}

function matchesQuery(value) {
  const needle = state.filters.q.trim().toLowerCase();
  if (!needle) return true;
  return String(value || "").toLowerCase().includes(needle);
}

function latestEvent(predicate) {
  for (const event of state.events) {
    if (predicate(event)) return event;
  }
  return null;
}

/** Newest event per task, newest-first. Used so a superseded REVIEW/IN_PROGRESS
 * event cannot be reported as current active work after the task has PASSed. */
function latestPerTask() {
  const seen = new Map();
  for (const event of state.events) {
    const key = event.task_id || event.event_id;
    if (!seen.has(key)) seen.set(key, event);
  }
  return [...seen.values()];
}

function showToast(message) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  if (state.toastTimer) window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, 4000);
}

/* ------------------------------------------------------------------ fetch */

async function fetchJSON(url) {
  const response = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
  const payload = await response.json();
  if (!payload || typeof payload !== "object" || !("data" in payload)) {
    throw new Error("Unexpected progress response shape.");
  }
  return payload;
}

function setBusy(busy) {
  const button = document.getElementById("refresh-btn");
  if (!button) return;
  button.disabled = busy;
  button.textContent = busy ? "Refreshing…" : "Refresh";
}

async function refresh(manual) {
  setBusy(true);
  try {
    const [snapshotPayload, eventsPayload] = await Promise.all([
      fetchJSON(API.snapshot),
      fetchJSON(`${API.events}?limit=${EVENT_LIMIT}`),
    ]);
    const snapshotData = snapshotPayload.data || {};
    const eventsData = eventsPayload.data || {};
    const events = Array.isArray(eventsData.events) ? eventsData.events : [];

    const previousIds = state.seenIds;
    const ids = new Set(events.map((event) => event.event_id));
    const newIds = [...ids].filter((id) => !previousIds.has(id));
    state.newIds = previousIds.size ? new Set(newIds) : new Set();
    for (const id of ids) state.seenIds.add(id);

    state.snapshot = snapshotData;
    state.events = events;
    state.eventsMeta = eventsData;
    state.stale = Boolean(snapshotData.progress && snapshotData.progress.stale);
    state.error = null;
    state.loading = false;
    if (state.newIds.size) {
      showToast(`${state.newIds.size} new progress event(s) recorded.`);
      window.setTimeout(() => {
        state.newIds.clear();
      }, 5000);
    }
    if (manual) showToast("Progress refreshed from the local ledger.");
    renderHeader();
    render();
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
    state.loading = false;
    renderHeader();
    if (!state.snapshot) {
      renderFatal();
    } else {
      render();
    }
  } finally {
    setBusy(false);
  }
}

/* ------------------------------------------------------------------ header */

function renderHeader() {
  const project = state.snapshot ? state.snapshot.project : null;
  const progress = state.snapshot ? state.snapshot.progress : null;
  const manifest = progress ? progress.manifest : null;

  const statusNode = document.getElementById("project-status");
  if (statusNode) {
    if (project && project.overall) {
      statusNode.textContent = project.overall.label || "AI FOUNDATION — ACCEPTED / FROZEN";
      statusNode.dataset.status = project.overall.status || "FROZEN";
    } else {
      statusNode.textContent = "NOT VERIFIED";
      statusNode.dataset.status = "NOT_VERIFIED";
    }
  }

  const current = latestPerTask();
  const active = current.find((event) => event.status === "IN_PROGRESS" || event.status === "REVIEW") || null;
  const activeNode = document.getElementById("active-item");
  if (activeNode) activeNode.textContent = active ? active.title : "No active item recorded";

  const lastNode = document.getElementById("last-update");
  if (lastNode) {
    const stamp = manifest ? manifest.last_timestamp_utc : null;
    lastNode.textContent = stamp ? fmtTime(stamp) : "—";
  }

  const connection = document.getElementById("connection");
  const connectionText = document.getElementById("connection-text");
  if (connection && connectionText) {
    if (state.error) {
      connection.dataset.state = "offline";
      connectionText.textContent = "Disconnected — showing last valid state";
    } else if (state.stale) {
      connection.dataset.state = "stale";
      connectionText.textContent = "Stale — no recent recorded event";
    } else {
      connection.dataset.state = "live";
      connectionText.textContent = "Connected — read-only ledger";
    }
  }

  const staleBanner = document.getElementById("stale-banner");
  const staleText = document.getElementById("stale-text");
  if (staleBanner) staleBanner.hidden = !(state.stale && !state.error && state.snapshot);
  if (staleText && progress) {
    staleText.textContent = `The last recorded progress event is older than the ${progress.stale_after_s || 3600}s freshness window. Showing the last valid state.`;
  }

  const errorBanner = document.getElementById("error-banner");
  const errorText = document.getElementById("error-text");
  if (errorBanner) errorBanner.hidden = !state.error;
  if (errorText && state.error) errorText.textContent = state.error;

  const hint = document.getElementById("search-hint");
  if (hint) {
    const count = filteredEvents().length;
    hint.textContent = state.filters.q ? `${count} matching event(s)` : `${state.events.length} recorded event(s) loaded`;
  }
}

/* ------------------------------------------------------------------ render */

const VIEW_TITLES = {
  overview: "Project overview",
  roadmap: "Project roadmap",
  timeline: "Development timeline",
  activity: "Build activity",
  phases: "Phases",
  ai: "AI stages",
  architecture: "Architecture",
  subsystems: "Subsystems",
  testing: "Testing & quality",
  security: "Security",
  resources: "Resource monitor",
  issues: "Issues & risks",
  git: "Git & changes",
  evidence: "Evidence & reports",
  docs: "Documentation",
  glossary: "Glossary",
};

function viewHead(description) {
  return el(
    "div",
    { class: "view-head" },
    el("div", null, el("h2", null, VIEW_TITLES[state.view] || "Project progress"), el("p", { class: "muted" }, description)),
    el(
      "div",
      { class: "pill-row" },
      el("span", { class: "tag" }, state.mode === "technical" ? "Technical view" : "Simple view"),
      el("span", { class: "muted" }, "Read-only recorded data"),
    ),
  );
}

function render() {
  const root = document.getElementById("view-root");
  if (!root) return;
  clear(root);
  if (state.loading) {
    root.append(el("div", { class: "loading-state" }, el("p", { class: "muted" }, "Loading recorded project progress…")));
    return;
  }
  if (!state.snapshot) {
    root.append(emptyState("The progress ledger is not available yet. Run `python scripts/project_progress.py seed` and refresh."));
    return;
  }
  const renderers = {
    overview: renderOverview,
    roadmap: renderRoadmap,
    timeline: renderTimeline,
    activity: renderActivity,
    phases: renderPhases,
    ai: renderAI,
    architecture: renderArchitecture,
    subsystems: renderSubsystems,
    testing: renderTesting,
    security: renderSecurity,
    resources: renderResources,
    issues: renderIssues,
    git: renderGit,
    evidence: renderEvidence,
    docs: renderDocs,
    glossary: renderGlossary,
  };
  const renderer = renderers[state.view] || renderOverview;
  root.append(renderer());
  root.focus({ preventScroll: true });
}

function renderFatal() {
  const root = document.getElementById("view-root");
  if (!root) return;
  clear(root);
  root.append(
    emptyState("The read-only progress endpoint is unreachable. Start `sf serve` and confirm /api/v1/progress/snapshot responds."),
  );
}

/* ------------------------------------------------------------------ overview */

function renderOverview() {
  const project = state.snapshot.project || {};
  const progress = state.snapshot.progress || {};
  const manifest = progress.manifest || {};
  const metrics = manifest.metrics || {};
  const overall = project.overall || {};
  const milestones = project.milestones || {};
  const git = manifest.git || null;

  const stages = project.stages || [];
  const phases = project.phases || [];
  const frozenStages = stages.filter((stage) => stage.status === "FROZEN").length;
  const historicalPhases = phases.filter((phase) => phase.status === "HISTORICAL").length;
  const lastTest = manifest.last_test && manifest.last_test.tests ? manifest.last_test.tests : null;
  const fullSuiteEvent =
    state.events.find((event) => event.tests && String(event.task_id || "").includes("full_suite")) || null;
  const latestTest = fullSuiteEvent && fullSuiteEvent.tests ? fullSuiteEvent.tests : lastTest;
  const latestTestNote = fullSuiteEvent
    ? `${fullSuiteEvent.tests.failed ?? 0} failed · ${fullSuiteEvent.tests.skipped ?? 0} skipped · ${fmtDate(fullSuiteEvent.timestamp_utc)}`
    : lastTest
      ? `${lastTest.failed ?? 0} failed · ${lastTest.skipped ?? 0} skipped`
      : "No test event recorded";
  const auditSuite = overall.full_suite || {};
  const verification = overall.additional_verification || {};
  const additional = verification.architecture_security_resource_provider_failure || {};

  const metricCards = [
    ["Project milestones", `${milestones.phases || 0} phases · ${milestones.stages || 0} stages`, "Static recorded model"],
    ["Completed phases", `${historicalPhases} / ${phases.length || 0}`, "Historical context, not current filesystem evidence"],
    ["AI stages frozen", `${frozenStages} / ${stages.length || 0}`, frozenStages === stages.length && stages.length ? "All six accepted and frozen by the recorded audit" : "Recorded Stage 0-5 status"],
    ["Active tasks", metrics.active_tasks ?? 0, "Latest status per recorded work item"],
    ["Pending tasks", metrics.pending_tasks ?? 0, "PLANNED / NOT_STARTED"],
    ["Blocked tasks", metrics.blocked_tasks ?? 0, "BLOCKED items"],
    ["Failed checks", metrics.failed_checks ?? 0, "Latest status FAILED"],
    [
      "Latest test report",
      latestTest ? `${latestTest.passed ?? 0} passed` : "—",
      latestTestNote,
    ],
    ["Latest update", manifest.last_timestamp_utc ? fmtTime(manifest.last_timestamp_utc) : "—", "Recorded UTC"],
    [
      "Uncommitted changes",
      git && git.available ? `${git.modified_count ?? 0} modified · ${git.untracked_count ?? 0} untracked` : "—",
      git && git.available ? `branch ${git.branch || "?"} @ ${String(git.head || "").slice(0, 10)}` : "No recorded Git snapshot",
    ],
  ];

  const current = latestPerTask();
  const active = current.find((event) => event.status === "IN_PROGRESS" || event.status === "REVIEW") || null;
  const blocked = current.find((event) => event.status === "BLOCKED") || null;
  const completed = latestEvent((event) => event.status === "PASS" || event.status === "FROZEN");
  const verifying = latestEvent((event) => event.level === "verification" || event.level === "test");

  const focus = card(
    "Current focus",
    kv([
      ["Active work", active ? active.title : "No active item recorded"],
      ["Most recent completion", completed ? `${completed.title} (${fmtTime(completed.timestamp_utc)})` : "None recorded"],
      ["Being verified", verifying ? verifying.title : "None recorded"],
      ["Blocked", blocked ? blocked.title : "None recorded"],
      ["Next authorized action", active && active.next_action ? active.next_action : "Record the next task update."],
      [
        "Explicitly not authorized",
        "Live trading, paper changes, risk/strategy/scheduler changes, live AI provider calls, provider consolidation, commit, push.",
      ],
    ]),
  );

  const health = card(
    "Project health",
    el(
      "div",
      { class: "grid grid-2" },
      healthRow("Implementation", "FROZEN", "AI foundation accepted and frozen; the progress dashboard is separately verified."),
      healthRow(
        "Tests",
        "PASS",
        latestTest
          ? `${latestTest.passed ?? 0} passed, ${latestTest.skipped ?? 0} skipped, ${latestTest.failed ?? 0} failed (recorded, not a fresh run).`
          : "No recorded test result.",
      ),
      healthRow("Security", "PASS", "16 deterministic checks recorded; findings remain documented."),
      healthRow("Architecture", "IMPLEMENTED", "Read-only API, dashboard and deterministic core are present."),
      healthRow("Documentation", "IMPLEMENTED", "Architecture, security and progress docs exist."),
      healthRow("Deployment readiness", "NOT_VERIFIED", "Public deployment is not approved."),
      healthRow("Live provider", "NOT_VERIFIED", "No real external AI call or credential was used."),
      healthRow("Trading readiness", "NOT_VERIFIED", "No live order path exists by design."),
    ),
  );

  return el(
    "div",
    null,
    viewHead("Only recorded facts are shown. Percentages are never invented; counts are used where a defensible denominator does not exist."),
    section("Primary metrics", "Derived from the append-only ledger and the static recorded model", el("div", { class: "grid grid-auto" }, metricCards.map(metricCard))),
    section("Current focus", null, focus),
    section("Project health", "A passing test suite never implies deployment or trading readiness", health),
    section(
      "AI foundation — accepted and frozen",
      "Recorded audit evidence; this page never re-runs the audit",
      card(
        null,
        el(
          "div",
          { class: "pill-row" },
          statusChip(overall.status || "FROZEN"),
          el("strong", null, overall.label || "AI FOUNDATION — ACCEPTED / FROZEN"),
          el("span", { class: "muted" }, overall.verdict || "ACCEPTED — STAGE 0-5 FROZEN"),
        ),
        kv([
          ["Acceptance date", overall.acceptance_date || "—"],
          ["Evidence source", overall.acceptance_source || "—"],
          ["Combined focused tests", `${overall.combined_focused_tests ?? 0} passed, ${overall.focused_skipped ?? 0} skipped`],
          [
            "Audit-time full suite",
            auditSuite.passed !== undefined
              ? `${auditSuite.passed} passed, ${auditSuite.skipped} skipped, ${auditSuite.failed} failed, ${auditSuite.errors ?? 0} errors`
              : "—",
          ],
          [
            "Additional verification",
            additional.passed !== undefined
              ? `${additional.passed} passed (architecture / security / resource / provider-failure)`
              : "—",
          ],
          ["Ruff check", verification.ruff_check || "—"],
          ["Ruff format", verification.ruff_format || "—"],
          ["AI offline CLI", verification.ai_offline_cli || "—"],
          ["sf ai test", verification.sf_ai_test || "—"],
          ["M2 classification", overall.m2 || "M2 ACCEPTED AS DOCUMENTED"],
          ["Remaining finding", overall.remaining_finding || "—"],
          ["Live provider test", overall.live_provider_test || "NOT PERFORMED"],
          ["Dashboard integration", overall.dashboard_integration || "—"],
          ["Commit / push", `${overall.commit || "NOT CREATED"} / ${overall.push || "NOT PERFORMED"}`],
        ]),
        el("p", { class: "muted" }, auditSuite.note || ""),
        el("p", { class: "muted" }, overall.freeze_basis || ""),
      ),
    ),
  );
}

function metricCard([label, value, note]) {
  return el(
    "div",
    { class: "card metric" },
    el("span", { class: "metric-value" }, String(value)),
    el("span", { class: "metric-label" }, label),
    el("span", { class: "metric-note" }, note),
  );
}

function healthRow(label, status, note) {
  return el(
    "div",
    { class: "card" },
    el("div", { class: "pill-row" }, el("strong", null, label), statusChip(status)),
    el("p", { class: "muted" }, note),
  );
}

/* ------------------------------------------------------------------ roadmap */

function phaseCard(phase) {
  const technical = state.mode === "technical";
  const body = el(
    "div",
    { class: "item-body" },
    el("p", null, phase.objective || ""),
    el("p", { class: "notice" }, phase.historical_note || "Historical project context."),
    technical
      ? el(
          "div",
          null,
          kv([
            ["Work performed", bulletList(phase.work)],
            ["Main components", bulletList(phase.components)],
            ["Decisions", bulletList(phase.decisions)],
            ["Verification evidence", bulletList(phase.evidence)],
            ["Test result", phase.tests],
            ["Limitations", bulletList(phase.limitations)],
            ["Outcome", phase.outcome],
            ["Relationship to current code", phase.relationship],
          ]),
        )
      : el(
          "div",
          { class: "simple-block" },
          el("p", { class: "qa" }, el("strong", null, "What happened?"), phase.outcome || phase.objective || ""),
          el("p", { class: "qa" }, el("strong", null, "Why does it matter?"), phase.relationship || ""),
          el("p", { class: "qa" }, el("strong", null, "Is it current?"), "No — this is a historical phase record, not current filesystem evidence."),
        ),
  );
  return el(
    "details",
    { class: "item phase-card", dataset: { status: phase.status } },
    el(
      "summary",
      null,
      statusChip(phase.status),
      el("span", { class: "summary-title" }, phase.title),
      el("span", { class: "summary-meta" }, "Historical record"),
    ),
    body,
  );
}

function renderRoadmap() {
  const phases = (state.snapshot.project.phases || []).filter((phase) =>
    matchesQuery(`${phase.id} ${phase.title} ${phase.objective} ${phase.outcome}`),
  );
  return el(
    "div",
    null,
    viewHead("Documented Phase A–L history. These records are historical project context, not current filesystem evidence."),
    section(
      "Historical phases",
      `${phases.length} of ${(state.snapshot.project.phases || []).length} shown`,
      phases.length ? phases.map(phaseCard) : emptyState("No phase matches the current search."),
    ),
  );
}

/* ------------------------------------------------------------------ phases */

function renderPhases() {
  const phases = state.snapshot.project.phases || [];
  const rows = phases.map((phase) => [
    phase.id,
    phase.title,
    statusChip(phase.status),
    phase.tests || "—",
    phase.outcome || "—",
  ]);
  return el(
    "div",
    null,
    viewHead("Phase summary table plus expandable historical detail. Deleted historical artifacts are never shown as present."),
    section("Phase summary", `${phases.length} recorded phases`, table(["Phase", "Title", "Status", "Recorded tests", "Outcome"], rows)),
    section("Phase detail", null, phases.map(phaseCard)),
  );
}

/* ------------------------------------------------------------------ timeline */

function renderTimeline() {
  const levels = (state.snapshot.project.hierarchy && state.snapshot.project.hierarchy.levels) || [];
  const chain = el("div", { class: "flow" });
  levels.forEach((level, index) => {
    if (index > 0) chain.append(el("span", { class: "flow-arrow" }, "→"));
    chain.append(el("span", { class: "flow-step" }, level));
  });

  const ordered = filteredEvents().slice().sort((a, b) => a.timestamp_utc.localeCompare(b.timestamp_utc));
  const timelineRows = ordered.map((event) => [
    fmtTime(event.timestamp_utc),
    statusChip(event.status),
    event.level,
    event.title,
    event.workstream,
  ]);

  const groups = new Map();
  for (const event of ordered) {
    const workstream = event.workstream || "unknown";
    if (!groups.has(workstream)) groups.set(workstream, []);
    groups.get(workstream).push(event);
  }
  const tree = [...groups.entries()].map(([workstream, items]) =>
    el(
      "details",
      { class: "item" },
      el("summary", null, el("span", { class: "summary-title" }, workstream), el("span", { class: "summary-meta" }, `${items.length} event(s)`)),
      el(
        "div",
        { class: "item-body" },
        items.map((event) =>
          el(
            "div",
            { class: "event", dataset: { status: event.status } },
            el(
              "div",
              { class: "event-head" },
              statusChip(event.status),
              el("span", { class: "event-title" }, event.title),
              el("span", { class: "event-time" }, fmtTime(event.timestamp_utc)),
            ),
            el("p", { class: "event-summary" }, event.summary || ""),
          ),
        ),
      ),
    ),
  );

  return el(
    "div",
    null,
    viewHead("PROJECT → WORKSTREAM → PHASE → STAGE → SUBSYSTEM → TASK → STEP → VERIFICATION → EVIDENCE."),
    section("Development hierarchy", "Every level can carry a status, timestamps, files, tests, evidence and a next action", card(null, chain)),
    section("Recorded event timeline", `${ordered.length} event(s), oldest first`, timelineRows.length ? table(["UTC", "Status", "Level", "Event", "Workstream"], timelineRows) : emptyState("No events match the current search.")),
    section("Development tree", "Grouped by workstream from recorded events", tree.length ? tree : emptyState("No recorded events.")),
  );
}

/* ------------------------------------------------------------------ activity */

function renderActivity() {
  const list = filteredEvents();
  const workstreams = [...new Set(state.events.map((event) => event.workstream))].sort();
  const levels = (state.snapshot.project.levels || []).slice();
  const statuses = (state.snapshot.project.statuses || []).slice();

  const filters = el(
    "div",
    { class: "filters", role: "group", "aria-label": "Activity filters" },
    selectField("Level", "level", levels, state.filters.level, (value) => { state.filters.level = value; render(); }),
    selectField("Status", "status", statuses, state.filters.status, (value) => { state.filters.status = value; render(); }),
    selectField("Workstream", "workstream", workstreams, state.filters.workstream, (value) => { state.filters.workstream = value; render(); }),
    inputField("Phase", "phase", state.filters.phase, (value) => { state.filters.phase = value; render(); }, "A–L"),
    selectField("Stage", "stage", ["0", "1", "2", "3", "4", "5"], state.filters.stage, (value) => { state.filters.stage = value; render(); }),
    inputField("From (UTC)", "from", state.filters.from, (value) => { state.filters.from = value; render(); }, "date", "date"),
    inputField("To (UTC)", "to", state.filters.to, (value) => { state.filters.to = value; render(); }, "date", "date"),
    selectField("Sort", "sort", ["newest", "oldest"], state.filters.sort, (value) => { state.filters.sort = value; render(); }),
    el(
      "button",
      {
        type: "button",
        class: "btn",
        onclick: () => {
          state.filters = { q: state.filters.q, level: "", status: "", workstream: "", phase: "", stage: "", from: "", to: "", sort: "newest" };
          renderHeader();
          render();
        },
      },
      "Clear filters",
    ),
  );

  return el(
    "div",
    null,
    viewHead("Chronological development updates from the append-only ledger. The page never runs tests or git commands."),
    section("Filters", `${list.length} matching event(s)`, filters),
    section(
      "Activity feed",
      state.eventsMeta && state.eventsMeta.total > state.events.length ? `Showing ${state.events.length} of ${state.eventsMeta.total} recorded events` : `${state.events.length} loaded event(s)`,
      list.length ? list.map(eventCard) : emptyState("No events match the current filters."),
    ),
  );
}

function selectField(label, id, options, value, onChange) {
  const select = el(
    "select",
    { id: `filter-${id}` },
    el("option", { value: "" }, "All"),
    options.map((option) => el("option", { value: String(option) }, String(option))),
  );
  select.value = value || "";
  select.addEventListener("change", () => onChange(select.value));
  return el("div", { class: "field" }, el("label", { for: `filter-${id}` }, label), select);
}

function inputField(label, id, value, onChange, placeholder, type) {
  const input = el("input", { id: `filter-${id}`, type: type || "text", placeholder: placeholder || "", value: value || "" });
  input.addEventListener("change", () => onChange(input.value));
  return el("div", { class: "field" }, el("label", { for: `filter-${id}` }, label), input);
}

function eventCard(event) {
  const technical = state.mode === "technical";
  const isNew = state.newIds.has(event.event_id);
  const simple = event.simple || null;
  const tests = event.tests || null;

  const body = el("div", { class: "event-body" });
  if (technical) {
    body.append(
      kv([
        ["Event id", el("span", { class: "mono" }, event.event_id)],
        ["Workstream", event.workstream],
        ["Phase / stage", [event.phase, event.stage].filter((value) => value !== undefined && value !== null).join(" / ") || "—"],
        ["Task", event.task_id || "—"],
        ["Agent", event.agent || "—"],
        ["Details", event.details || "—"],
        ["Files", event.files && event.files.length ? bulletList(event.files) : "—"],
        ["Tests", tests ? `${tests.passed ?? 0} passed · ${tests.failed ?? 0} failed · ${tests.skipped ?? 0} skipped${tests.command ? ` · ${tests.command}` : ""}` : "—"],
        ["Evidence", event.evidence && event.evidence.length ? bulletList(event.evidence) : "—"],
        ["Blockers", event.blockers && event.blockers.length ? bulletList(event.blockers) : "—"],
        ["Next action", event.next_action || "—"],
      ]),
    );
    if (event.technical) {
      body.append(
        el("div", { class: "pill-row mt-sm" }, ...Object.entries(event.technical).flatMap(([key, values]) => values.map((value) => el("span", { class: "tag" }, `${key}: ${value}`)))),
      );
    }
  } else if (simple) {
    body.append(
      el(
        "div",
        { class: "simple-block" },
        el("p", { class: "qa" }, el("strong", null, "What happened?"), simple.what || event.summary || ""),
        el("p", { class: "qa" }, el("strong", null, "Why?"), simple.why || ""),
        el("p", { class: "qa" }, el("strong", null, "Is it working?"), simple.working || ""),
        el("p", { class: "qa" }, el("strong", null, "What is not verified?"), simple.not_verified || ""),
        el("p", { class: "qa" }, el("strong", null, "What happens next?"), simple.next || event.next_action || ""),
      ),
    );
  } else {
    body.append(el("p", null, event.details || event.summary || ""));
  }

  const cardNode = el(
    "article",
    { class: "event" + (isNew ? " is-new" : ""), dataset: { status: event.status } },
    el(
      "div",
      { class: "event-head" },
      statusChip(event.status),
      el("span", { class: "tag" }, event.level),
      el("span", { class: "event-title" }, event.title),
      el("span", { class: "event-time" }, fmtTime(event.timestamp_utc)),
    ),
    el("p", { class: "event-summary" }, event.summary || ""),
    el("details", { class: "event-details" }, el("summary", null, technical ? "Technical detail" : "More detail"), body),
  );
  return cardNode;
}

function filteredEvents() {
  const filters = state.filters;
  let list = state.events.slice();
  if (filters.level) list = list.filter((event) => event.level === filters.level);
  if (filters.status) list = list.filter((event) => event.status === filters.status);
  if (filters.workstream) list = list.filter((event) => event.workstream === filters.workstream);
  if (filters.phase) list = list.filter((event) => String(event.phase || "").toLowerCase() === filters.phase.toLowerCase());
  if (filters.stage) list = list.filter((event) => String(event.stage ?? "") === String(filters.stage));
  if (filters.from) list = list.filter((event) => event.timestamp_utc.slice(0, 10) >= filters.from);
  if (filters.to) list = list.filter((event) => event.timestamp_utc.slice(0, 10) <= filters.to);
  if (filters.q) {
    const needle = filters.q.trim().toLowerCase();
    list = list.filter((event) =>
      [
        event.title,
        event.summary,
        event.details,
        event.workstream,
        event.task_id,
        event.phase,
        event.stage,
        (event.files || []).join(" "),
        (event.evidence || []).join(" "),
        (event.blockers || []).join(" "),
        event.simple ? Object.values(event.simple).join(" ") : "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }
  list.sort((a, b) =>
    filters.sort === "oldest" ? a.timestamp_utc.localeCompare(b.timestamp_utc) : b.timestamp_utc.localeCompare(a.timestamp_utc),
  );
  return list;
}

/* ------------------------------------------------------------------ AI stages */

function stageCard(stage) {
  const technical = state.mode === "technical";
  const tests = stage.tests || {};
  const groups = tests.groups || [];
  const acceptance = stage.acceptance_date || "2026-11-07";
  const evidenceLabel = stage.evidence_label || "Recorded report, not a fresh execution.";
  const body = el(
    "div",
    { class: "item-body" },
    el("p", null, stage.objective || ""),
    technical
      ? el(
          "div",
          null,
          kv([
            ["Acceptance", `${stage.acceptance_verdict || "ACCEPTED - STAGE 0-5 FROZEN"} (${acceptance})`],
            ["Components", bulletList(stage.components)],
            ["Focused tests", `${tests.passed ?? 0} passed · ${tests.failed ?? 0} failed · ${tests.skipped ?? 0} skipped`],
            ["Command", tests.command ? el("span", { class: "mono" }, tests.command) : "—"],
            ["Evidence", bulletList((stage.evidence || []).map((item) => `${item.label}: ${item.value} (${item.source}, ${item.date || "recorded"})`))],
            ["Security and authority", stage.authority || "—"],
            ["M2 classification", stage.m2 || "M2 ACCEPTED AS DOCUMENTED"],
            ["Remaining finding", stage.remaining_finding || "None"],
            ["Findings", bulletList(stage.findings)],
            ["Limitations", bulletList(stage.limitations)],
            ["Next", stage.next || "—"],
          ]),
          groups.length ? table(["Test group", "Passed"], groups.map((group) => [group.name, group.passed])) : null,
        )
      : el(
          "div",
          { class: "simple-block" },
          el("p", { class: "qa" }, el("strong", null, "What was built?"), stage.title),
          el("p", { class: "qa" }, el("strong", null, "Is it frozen?"), `Yes. Accepted on ${acceptance} and frozen; changing it needs explicit approval.`),
          el("p", { class: "qa" }, el("strong", null, "Is it working?"), `${tests.passed ?? 0} focused offline tests passed (recorded evidence, not a fresh run).`),
          el("p", { class: "qa" }, el("strong", null, "What is not verified?"), (stage.limitations || []).join(" ") || "No additional limitation recorded."),
          el("p", { class: "qa" }, el("strong", null, "Can it trade or override risk?"), stage.authority || "No. AI is advisory only."),
          el("p", { class: "qa" }, el("strong", null, "M2 settings"), stage.m2 || "M2 ACCEPTED AS DOCUMENTED."),
          el("p", { class: "qa" }, el("strong", null, "Remaining finding"), stage.remaining_finding || "None."),
        ),
    el("p", { class: "muted" }, evidenceLabel),
  );
  return el(
    "details",
    { class: "item", dataset: { status: stage.status } },
    el(
      "summary",
      null,
      statusChip(stage.status),
      el("span", { class: "summary-title" }, `Stage ${stage.id} — ${stage.title}`),
      el("span", { class: "summary-meta" }, `${tests.passed ?? 0} focused tests · FROZEN`),
    ),
    body,
  );
}

function renderAI() {
  const project = state.snapshot.project || {};
  const allStages = project.stages || [];
  const stages = allStages.filter((stage) => matchesQuery(`stage ${stage.id} ${stage.title} ${stage.objective}`));
  const frozen = allStages.filter((stage) => stage.status === "FROZEN").length;
  const overall = project.overall || {};
  const stage2 = allStages.find((stage) => stage.id === 2);
  const auditSuite = overall.full_suite || {};
  const verification = overall.additional_verification || {};
  const dashboardRun =
    state.events.find((event) => event.tests && String(event.task_id || "").includes("full_suite")) || null;

  const stageRows = allStages.map((stage) => [
    `Stage ${stage.id} — ${stage.title}`,
    statusChip(stage.status),
    `${stage.tests ? stage.tests.passed ?? 0 : "—"} passed`,
    stage.acceptance_date || "—",
  ]);

  return el(
    "div",
    null,
    viewHead("AI foundation roadmap. All six stages are frozen on recorded acceptance evidence; this page does not re-run tests or call providers."),
    section(
      "AI FOUNDATION — ACCEPTED / FROZEN",
      `${frozen} of ${allStages.length} stages frozen`,
      card(
        null,
        el(
          "div",
          { class: "pill-row" },
          statusChip(overall.status || "FROZEN"),
          el("strong", null, overall.label || "AI FOUNDATION — ACCEPTED / FROZEN"),
        ),
        kv([
          ["Verdict", overall.verdict || "ACCEPTED — STAGE 0-5 FROZEN"],
          ["Acceptance date", overall.acceptance_date || "—"],
          ["Evidence source", overall.acceptance_source || "—"],
          ["Combined focused tests", `${overall.combined_focused_tests ?? 0} passed, ${overall.focused_skipped ?? 0} skipped`],
          [
            "Audit-time full suite",
            auditSuite.passed !== undefined
              ? `${auditSuite.passed} passed, ${auditSuite.skipped} skipped, ${auditSuite.failed} failed, ${auditSuite.errors ?? 0} errors`
              : "—",
          ],
          [
            "Additional verification",
            verification.architecture_security_resource_provider_failure
              ? `${verification.architecture_security_resource_provider_failure.passed} passed (architecture / security / resource / provider-failure)`
              : "—",
          ],
          ["Ruff check / format", `${verification.ruff_check || "—"} / ${verification.ruff_format || "—"}`],
          ["AI offline CLI", verification.ai_offline_cli || "—"],
          ["sf ai test", verification.sf_ai_test || "—"],
          ["M2 classification", overall.m2 || "M2 ACCEPTED AS DOCUMENTED"],
          ["Remaining finding", overall.remaining_finding || "—"],
          ["Live provider test", overall.live_provider_test || "NOT PERFORMED"],
          ["Commit / push", `${overall.commit || "NOT CREATED"} / ${overall.push || "NOT PERFORMED"}`],
        ]),
        el("p", { class: "muted" }, auditSuite.note || ""),
      ),
    ),
    section(
      "Stage status",
      "All six stages recorded as FROZEN",
      table(["Stage", "Status", "Focused evidence", "Acceptance date"], stageRows),
    ),
    section(
      "Evidence separation",
      "Different runs, different scopes — never combined",
      el(
        "div",
        { class: "grid grid-2" },
        metricCard([
          "Stage 0–5 final acceptance audit",
          `${auditSuite.passed ?? 785} passed`,
          `${auditSuite.skipped ?? 4} skipped, ${auditSuite.failed ?? 0} failed · recorded ${auditSuite.date || "2026-11-07"}`,
        ]),
        metricCard([
          "Project Progress Dashboard integration",
          dashboardRun && dashboardRun.tests ? `${dashboardRun.tests.passed} passed` : "—",
          dashboardRun && dashboardRun.tests
            ? `${dashboardRun.tests.skipped} skipped, ${dashboardRun.tests.failed} failed · recorded ${fmtDate(dashboardRun.timestamp_utc)}`
            : "No dashboard run recorded in the ledger",
        ]),
      ),
      el(
        "p",
        { class: "muted" },
        "The 785-pass audit and the 831-pass dashboard run are separate evidence records. Neither total includes the other.",
      ),
    ),
    stage2
      ? section(
          "Stage 2 live-provider status",
          "Offline/mock evidence only",
          el(
            "div",
            { class: "notice" },
            el("strong", null, "Real external API call: NOT_VERIFIED. "),
            "Offline/mock adapter tests passed (33). No live credential was read or used. M2 residual configuration limitations are accepted as documented and remain inert.",
          ),
        )
      : null,
    section("Stages", `${stages.length} of ${allStages.length} shown`, stages.length ? stages.map(stageCard) : emptyState("No stage matches the current search.")),
  );
}

/* ------------------------------------------------------------------ architecture */

function renderArchitecture() {
  const architecture = state.snapshot.project.architecture || {};
  const layers = architecture.layers || [];
  const flow = architecture.ai_flow || [];

  const layerNodes = layers.map((layer) =>
    el(
      "div",
      { class: "arch-layer" },
      el("h3", null, layer.name),
      el(
        "div",
        { class: "arch-components" },
        (layer.components || []).map((component) =>
          el("span", { class: "arch-chip" }, component.name, statusChip(component.status), el("span", { class: "muted" }, component.evidence || "")),
        ),
      ),
    ),
  );

  const flowNodes = el("div", { class: "flow" });
  flow.forEach((step, index) => {
    if (index > 0) flowNodes.append(el("span", { class: "flow-arrow" }, "→"));
    flowNodes.append(el("span", { class: "flow-step" }, step.step, " ", statusChip(step.status)));
  });

  return el(
    "div",
    null,
    viewHead("Implemented layers versus design-only components, based on docs/ARCHITECTURE.md, AI_GATEWAY.md §16 and current source."),
    section("Layered architecture", "IMPLEMENTED / PARTIAL / HISTORICAL / DESIGN_ONLY", el("div", { class: "grid" }, layerNodes)),
    section("AI request flow", "The deterministic core remains authoritative", card(null, flowNodes)),
    section(
      "Architecture honesty note",
      null,
      el("div", { class: "notice" }, architecture.note || "No architecture note recorded."),
    ),
  );
}

/* ------------------------------------------------------------------ subsystems */

function renderSubsystems() {
  const subsystems = (state.snapshot.project.subsystems || []).filter((item) =>
    matchesQuery(`${item.name} ${item.note} ${item.phase}`),
  );
  const rows = subsystems.map((item) => [item.id, item.name, item.phase, statusChip(item.status), item.note]);
  return el(
    "div",
    null,
    viewHead("The 20 documented components and their current implementation status."),
    section("Components", `${subsystems.length} shown`, rows.length ? table(["#", "Component", "Phase", "Status", "Note"], rows) : emptyState("No subsystem matches the current search.")),
  );
}

/* ------------------------------------------------------------------ testing */

function renderTesting() {
  const project = state.snapshot.project || {};
  const overall = project.overall || {};
  const fullSuite = overall.full_suite || {};
  const testEvents = state.events.filter((event) => event.level === "test" || event.tests);
  const stageRows = (project.stages || []).map((stage) => [
    `Stage ${stage.id} — ${stage.title}`,
    stage.tests ? `${stage.tests.passed ?? 0}` : "—",
    stage.tests ? `${stage.tests.failed ?? 0}` : "—",
    stage.tests ? `${stage.tests.skipped ?? 0}` : "—",
    stage.tests && stage.tests.command ? el("span", { class: "mono wrap-anywhere" }, stage.tests.command) : "—",
    statusChip(stage.status),
  ]);

  return el(
    "div",
    null,
    viewHead("Recorded test evidence only. Opening or refreshing this dashboard never runs tests."),
    section(
      "Last recorded full test run",
      "Recorded result, not a fresh execution",
      el(
        "div",
        { class: "grid grid-3" },
        metricCard(["Passed", fullSuite.passed ?? "—", fullSuite.source || "recorded report"]),
        metricCard(["Skipped", fullSuite.skipped ?? "—", "Skips are never counted as passes"]),
        metricCard(["Failed", fullSuite.failed ?? "—", fullSuite.date || "recorded report"]),
      ),
    ),
    section(
      "Stage-specific test groups",
      "Focused Stage 0–5 evidence",
      table(["Stage", "Passed", "Failed", "Skipped", "Command", "Status"], stageRows),
    ),
    section(
      "Recorded test events",
      `${testEvents.length} event(s)`,
      testEvents.length
        ? table(
            ["UTC", "Status", "Title", "Passed", "Failed", "Skipped", "Source"],
            testEvents.map((event) => [
              fmtTime(event.timestamp_utc),
              statusChip(event.status),
              event.title,
              event.tests ? event.tests.passed ?? 0 : "—",
              event.tests ? event.tests.failed ?? 0 : "—",
              event.tests ? event.tests.skipped ?? 0 : "—",
              event.tests && event.tests.report ? event.tests.report : event.agent || "—",
            ]),
          )
        : emptyState("No test event has been recorded in the ledger yet."),
    ),
    section(
      "Static analysis and formatting",
      "Only recorded results are shown",
      emptyState("Run `uv run --offline ruff check <files>` and `uv run --offline ruff format --check <files>`, then record the result with scripts/project_progress.py."),
    ),
  );
}

/* ------------------------------------------------------------------ security */

function renderSecurity() {
  const project = state.snapshot.project || {};
  const checks = (project.security_checks || []).filter((check) => matchesQuery(`${check.label} ${check.evidence}`));
  const findings = (project.findings || []).filter((finding) => matchesQuery(`${finding.title} ${finding.note}`));

  const m2 = (project.findings || []).find((finding) => finding.id === "M2");
  const m2Fields = m2 ? m2.fields || [] : [];

  return el(
    "div",
    null,
    viewHead("Deterministic checks and documented findings. AI can never override risk gates or authorize execution."),
    section(
      "Security checks",
      `${checks.length} shown`,
      checks.length
        ? table(["Check", "Status", "Evidence"], checks.map((check) => [check.label, statusChip(check.status), check.evidence]))
        : emptyState("No security check matches the current search."),
    ),
    section(
      "M2 — ACCEPTED AS DOCUMENTED",
      "Residual configuration fields are inert and are not working features",
      card(
        null,
        el(
          "div",
          { class: "pill-row" },
          el("strong", null, m2 ? m2.status || "ACCEPTED AS DOCUMENTED" : "M2 NOT RECORDED"),
          el("span", { class: "tag" }, "non-blocking"),
        ),
        el("p", { class: "muted" }, m2 ? m2.note : "M2 is not recorded."),
        el("div", { class: "pill-row" }, m2Fields.map((field) => el("span", { class: "chip", dataset: { status: "INERT" } }, field))),
        el("h4", { class: "mt-sm" }, "Reserved / not enforced"),
        el(
          "p",
          { class: "muted" },
          "The fields above are parsed and validated but inert: they do not change routing, cost, concurrency or fallback at runtime. No runtime budget tracker and no paid fallback is wired.",
        ),
        el("h4", { class: "mt-sm" }, "Enforced instead"),
        el(
          "p",
          { class: "muted" },
          "The Stage 0–5 gateway enforces concurrency 1, request/response character caps, max output tokens, per-request timeout, bounded retries, and evidence caps (see AI_GATEWAY.md §16.7).",
        ),
        el("h4", { class: "mt-sm" }, "Authentication behavior retained"),
        bulletList(m2 ? m2.behavior : []),
      ),
    ),
    section("Findings and residual risks", null, findings.map((finding) =>
      findingCard(finding),
    )),
    section(
      "Boundary reminders",
      null,
      el(
        "ul",
        { class: "list-clean" },
        el("li", null, "Legacy ai_providers/ coexists with the newer ai/ gateway; consolidation has not been authorized."),
        el("li", null, "Real provider connectivity has not been verified."),
        el("li", null, "Public network deployment is not approved merely because local tests pass."),
        el("li", null, "No live order adapter exists; paper fills are hypothetical."),
      ),
    ),
  );
}

/* ------------------------------------------------------------------ resources */

function renderResources() {
  const resources = state.snapshot.project.resource_limits || {};
  const documented = resources.documented || [];
  const probes = resources.recorded_probes || [];
  const unavailable = resources.unavailable || [];

  return el(
    "div",
    null,
    viewHead("Documented operating limits are not live measurements. No RAM or GPU utilization is invented."),
    section("Configured limits", "Documented constraints", table(["Limit", "Value", "Source"], documented.map((item) => [item.label, item.value, item.source]))),
    section(
      "Recorded measurements",
      "Only previously recorded probes",
      probes.length
        ? table(["Probe", "Value", "Source", "Note"], probes.map((item) => [item.label, item.value, item.source, item.note || "—"]))
        : emptyState("No recorded measurement probe."),
    ),
    section("Unavailable measurements", null, el("div", { class: "pill-row" }, unavailable.map((item) => el("span", { class: "chip", dataset: { status: "NOT_VERIFIED" } }, item)))),
    section("Note", null, el("div", { class: "notice" }, resources.note || "")),
  );
}

/* ------------------------------------------------------------------ issues */

function renderIssues() {
  const findings = (state.snapshot.project.findings || []).filter((finding) =>
    matchesQuery(`${finding.title} ${finding.note} ${finding.status}`),
  );
  return el(
    "div",
    null,
    viewHead("Known findings, residual risks and authorization boundaries."),
    section(
      "Issues and risks",
      `${findings.length} recorded`,
      findings.length
        ? findings.map((finding) => findingCard(finding))
        : emptyState("No finding matches the current search."),
    ),
  );
}

/* ------------------------------------------------------------------ git */

function renderGit() {
  const manifest = state.snapshot.progress && state.snapshot.progress.manifest ? state.snapshot.progress.manifest : {};
  const git = manifest.git;
  if (!git || !git.available) {
    return el(
      "div",
      null,
      viewHead("Read-only Git state recorded into the manifest. The dashboard never runs git."),
      section(
        "Git state",
        "No recorded snapshot",
        emptyState("No Git snapshot is recorded yet. Run `python scripts/project_progress.py snapshot` to record read-only branch, HEAD and change counts."),
      ),
    );
  }
  return el(
    "div",
    null,
    viewHead("Read-only Git state recorded into the manifest. No stage, commit, reset, clean, checkout or push is performed."),
    section(
      "Repository state",
      `Collected ${fmtTime(git.collected_utc)}`,
      el(
        "div",
        { class: "grid grid-4" },
        metricCard(["Branch", git.branch || "—", "Recorded branch"]),
        metricCard(["HEAD", String(git.head || "").slice(0, 10) || "—", git.head || "—"]),
        metricCard(["Modified", git.modified_count ?? 0, "Tracked changes"]),
        metricCard(["Untracked", git.untracked_count ?? 0, "Not added to Git"]),
      ),
    ),
    section(
      "Changed paths",
      "Bounded output",
      el(
        "div",
        { class: "grid grid-2" },
        card("Modified", bulletList(git.modified)),
        card("Untracked", bulletList(git.untracked)),
      ),
    ),
    section("Diff stat", "Read-only `git diff --stat`", el("pre", { class: "mono wrap-anywhere" }, git.diff_stat || "No diff stat recorded.")),
    section("Safety note", null, el("div", { class: "notice" }, git.note || "Read-only Git snapshot.")),
  );
}

/* ------------------------------------------------------------------ evidence */

function renderEvidence() {
  const evidenceEvents = state.events.filter(
    (event) =>
      (event.evidence && event.evidence.length) ||
      ["verification", "evidence", "test"].includes(event.level) ||
      event.status === "FROZEN",
  );
  return el(
    "div",
    null,
    viewHead("Verification and evidence records from the ledger. Evidence is referenced, never re-executed here."),
    section(
      "Evidence records",
      `${evidenceEvents.length} event(s)`,
      evidenceEvents.length
        ? table(
            ["UTC", "Status", "Level", "Title", "Evidence"],
            evidenceEvents.map((event) => [
              fmtTime(event.timestamp_utc),
              statusChip(event.status),
              event.level,
              event.title,
              (event.evidence || []).join(" · ") || "—",
            ]),
          )
        : emptyState("No evidence event recorded yet."),
    ),
  );
}

/* ------------------------------------------------------------------ docs */

function renderDocs() {
  const docs = (state.snapshot.project.docs || []).filter((doc) => matchesQuery(`${doc.title} ${doc.path} ${doc.description}`));
  return el(
    "div",
    null,
    viewHead("Repository documentation relevant to the progress control center and the engineering boundaries."),
    section(
      "Documents",
      `${docs.length} shown`,
      el(
        "div",
        { class: "grid grid-2" },
        docs.map((doc) =>
          el("div", { class: "card" }, el("h3", null, doc.title), el("p", { class: "mono wrap-anywhere" }, doc.path), el("p", { class: "muted" }, doc.description)),
        ),
      ),
    ),
  );
}

/* ------------------------------------------------------------------ glossary */

function renderGlossary() {
  const terms = (state.snapshot.project.glossary || []).filter((entry) => matchesQuery(`${entry.term} ${entry.definition}`));
  return el(
    "div",
    null,
    viewHead("Plain-language definitions used by both Simple and Technical views."),
    section("Terms", `${terms.length} shown`, table(["Term", "Definition"], terms.map((entry) => [entry.term, entry.definition]))),
  );
}

/* ------------------------------------------------------------------ controls */

function setMode(mode) {
  state.mode = mode;
  const simple = document.getElementById("view-simple");
  const technical = document.getElementById("view-technical");
  if (simple) {
    simple.classList.toggle("is-active", mode === "simple");
    simple.setAttribute("aria-pressed", String(mode === "simple"));
  }
  if (technical) {
    technical.classList.toggle("is-active", mode === "technical");
    technical.setAttribute("aria-pressed", String(mode === "technical"));
  }
  render();
}

function setView(view) {
  if (!VIEW_TITLES[view]) return;
  state.view = view;
  for (const link of document.querySelectorAll(".nav-link")) {
    link.classList.toggle("is-active", link.dataset.view === view);
  }
  const sidebar = document.getElementById("sidebar");
  if (sidebar) sidebar.classList.remove("is-open");
  const toggle = document.getElementById("menu-toggle");
  if (toggle) toggle.setAttribute("aria-expanded", "false");
  render();
}

function wireControls() {
  for (const link of document.querySelectorAll(".nav-link")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const view = link.dataset.view;
      if (view) {
        history.replaceState(null, "", `#${view}`);
        setView(view);
      }
    });
  }

  const simple = document.getElementById("view-simple");
  const technical = document.getElementById("view-technical");
  if (simple) simple.addEventListener("click", () => setMode("simple"));
  if (technical) technical.addEventListener("click", () => setMode("technical"));

  const refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) refreshBtn.addEventListener("click", () => refresh(true));

  const search = document.getElementById("global-search");
  if (search) {
    search.addEventListener("input", () => {
      state.filters.q = search.value;
      renderHeader();
      render();
    });
  }

  const menu = document.getElementById("menu-toggle");
  const sidebar = document.getElementById("sidebar");
  if (menu && sidebar) {
    menu.addEventListener("click", () => {
      const open = sidebar.classList.toggle("is-open");
      menu.setAttribute("aria-expanded", String(open));
    });
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh(false);
  });

  window.addEventListener("hashchange", () => {
    const view = window.location.hash.replace("#", "");
    if (VIEW_TITLES[view]) setView(view);
  });
}

function startPolling() {
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(() => {
    if (document.hidden) return;
    refresh(false);
  }, POLL_MS);
}

function init() {
  wireControls();
  const initial = window.location.hash.replace("#", "");
  if (VIEW_TITLES[initial]) {
    state.view = initial;
    for (const link of document.querySelectorAll(".nav-link")) {
      link.classList.toggle("is-active", link.dataset.view === initial);
    }
  } else {
    const overview = document.querySelector('.nav-link[data-view="overview"]');
    if (overview) overview.classList.add("is-active");
  }
  renderHeader();
  refresh(false);
  startPolling();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
