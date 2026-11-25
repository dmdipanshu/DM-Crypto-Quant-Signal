/* SignalForge — Monitoring dashboard (vanilla JavaScript).
 *
 * No framework, no external requests, no HTML string injection. All dynamic
 * text is written with textContent / createElement. Reads only GET-only API
 * endpoints and never triggers tests, Git writes, training, AI calls, or trading.
 */
"use strict";

const API = {
  health: "/api/v1/health",
  paperStatus: "/api/v1/paper/status",
  paperState: "/api/v1/paper/state",
  paperTrades: "/api/v1/paper/trades?limit=50",
  paperLedger: "/api/v1/paper/ledger?limit=200",
  signalsLatest: "/api/v1/signals/latest",
  signals: "/api/v1/signals?limit=20",
  q5Collection: "/api/v1/q5/collection/status",
  q5Governance: "/api/v1/q5/prospective/governance",
  aiStatus: "/api/v1/ai/status",
  aiMetrics: "/api/v1/ai/metrics",
  audit: "/api/v1/audit/status",
  events: "/api/v1/progress/events?limit=50",
};
const POLL_MS = 5000;

const Q5_REQUIRED_OBSERVATIONS = 168;
const Q5_REQUIRED_COVERAGE = 95;
const Q5_REQUIRED_DURATION_DAYS = 7;

const state = {
  token: null,
  authenticated: false,
  authRequired: false,
  loading: true,
  refreshing: false,
  timer: null,
  health: null,
  paperStatus: null,
  paperState: null,
  paperTrades: null,
  paperLedger: null,
  signalsLatest: null,
  signals: null,
  q5Collection: null,
  q5Governance: null,
  aiStatus: null,
  aiMetrics: null,
  audit: null,
  events: null,
  lastUpdated: null,
};

class AuthError extends Error {
  constructor(message) {
    super(message);
    this.name = "AuthError";
  }
}

/* ------------------------------------------------------------------ token storage (session only) */

function getToken() {
  try {
    const value = sessionStorage.getItem("signalforge_api_token");
    return value || null;
  } catch {
    return null;
  }
}

function setToken(value) {
  try {
    sessionStorage.setItem("signalforge_api_token", value);
  } catch {
    /* sessionStorage unavailable; token lives only in memory this session */
  }
  state.token = value;
  state.authenticated = !!value;
}

function clearToken() {
  try {
    sessionStorage.removeItem("signalforge_api_token");
  } catch {
    /* ignore */
  }
  state.token = null;
  state.authenticated = false;
}

/* Validate an entered token against the authenticated health endpoint. */
async function validateToken(token) {
  if (!token) return false;
  try {
    const response = await fetch(API.health, {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        Authorization: "Bearer " + token,
      },
    });
    return response.ok;
  } catch {
    return false;
  }
}

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

function get(id) {
  return document.getElementById(id);
}

function set(id, value) {
  const node = get(id);
  if (node) node.textContent = value;
}

function show(id) {
  const node = get(id);
  if (node) node.hidden = false;
}

function hide(id) {
  const node = get(id);
  if (node) node.hidden = true;
}

function text(value) {
  if (value === null || value === undefined || value === "") return "N/A";
  return String(value);
}

function usd(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "N/A";
  return Number(value).toLocaleString(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "N/A";
  return Number(value).toLocaleString(undefined, { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct2(numerator, denominator) {
  if (denominator === null || denominator === undefined || denominator === 0 || !Number.isFinite(Number(numerator)) || !Number.isFinite(Number(denominator))) return "N/A";
  return ((Number(numerator) / Number(denominator)) * 100).toLocaleString(undefined, { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtIst(iso) {
  if (!iso) return "N/A";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "N/A";
    const parts = new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    }).formatToParts(d);
    let day = "", month = "", year = "", time = "", ampm = "";
    for (const p of parts) {
      if (p.type === "day") day = p.value;
      else if (p.type === "month") month = p.value;
      else if (p.type === "year") year = p.value;
      else if (p.type === "hour") time = p.value;
      else if (p.type === "minute") time += ":" + p.value;
      else if (p.type === "second") time += ":" + p.value;
      else if (p.type === "dayPeriod") ampm = p.value.toUpperCase();
    }
    return `${day} ${month} ${year}, ${time} ${ampm} IST`;
  } catch {
    return "N/A";
  }
}

function statusChip(label, status) {
  const chip = el("span", { class: "chip", dataset: { status: String(status || "N/A") } }, text(label));
  return chip;
}

function table(headers, rows) {
  const thead = el("thead", null, el("tr", null, headers.map((h) => el("th", { scope: "col" }, h))));
  const tbody = el("tbody");
  for (const row of rows) {
    tbody.append(el("tr", null, row.map((cell) => el("td", null, cell instanceof Node ? cell : text(cell)))));
  }
  return el("div", { class: "table-wrap" }, el("table", null, thead, tbody));
}

function emptyState(message) {
  return el("div", { class: "empty" }, message);
}

function kv(pairs) {
  const dl = el("dl", { class: "kv" });
  for (const [key, value] of pairs) {
    if (value === null || value === undefined) continue;
    dl.append(el("dt", null, key), el("dd", null, text(value)));
  }
  return dl;
}

/* ------------------------------------------------------------------ data extraction helpers */

function safeGet(envelope, path) {
  let node = envelope;
  for (const key of path) {
    if (node === null || node === undefined || typeof node !== "object") return undefined;
    node = node[key];
  }
  return node;
}

function envelopeHasError(envelope) {
  return envelope && typeof envelope === "object" && "error" in envelope && envelope.error !== null && envelope.error !== undefined;
}

function extractData(envelope) {
  return envelope && typeof envelope === "object" ? envelope.data : undefined;
}

async function fetchJSON(url) {
  const headers = { Accept: "application/json" };
  const token = state.token || getToken();
  if (token) headers["Authorization"] = "Bearer " + token;
  const response = await fetch(url, { cache: "no-store", headers });
  if (response.status === 401) {
    clearToken();
    stopPolling();
    throw new AuthError("Authentication required (HTTP 401)");
  }
  if (!response.ok) {
    const err = new Error(`HTTP ${response.status} for ${url}`);
    err.status = response.status;
    throw err;
  }
  return await response.json();
}

async function fetchResult(url) {
  try {
    const payload = await fetchJSON(url);
    if (envelopeHasError(payload)) {
      const msg = payload && payload.error && (payload.error.message || payload.error.code)
        ? String(payload.error.message || payload.error.code)
        : "Request failed";
      return { kind: "error", status: 200, payload, data: null, reason: msg };
    }
    return { kind: "ok", status: 200, payload, data: extractData(payload) };
  } catch (error) {
    if (error instanceof AuthError) return { kind: "auth", status: 401, payload: null, data: null, reason: "auth_required" };
    if (error && typeof error.status === "number") {
      return { kind: "http", status: error.status, payload: null, data: null, reason: `HTTP ${error.status}` };
    }
    return { kind: "network", status: 0, payload: null, data: null, reason: "network_error" };
  }
}

function resultValue(result) {
  if (!result || typeof result !== "object") return "N/A";
  if (result.kind === "network") return "OFFLINE";
  if (result.kind === "auth") return "AUTH REQUIRED";
  if (result.kind === "http" || result.kind === "error") return "UNAVAILABLE";
  if (result.data === undefined || result.data === null) return "N/A";
  return result.data;
}

function resultReason(result) {
  if (!result || typeof result !== "object") return "N/A";
  if (result.reason) return String(result.reason);
  if (result.kind === "network") return "network_error";
  if (result.kind === "auth") return "auth_required";
  return "N/A";
}

function setStatus(id, value, status) {
  const node = get(id);
  if (!node) return;
  node.textContent = value;
  if (status !== undefined && status !== null) node.dataset.status = String(status);
}

function setMetric(id, value, status) {
  const node = get(id);
  if (!node) return;
  node.textContent = value;
  if (status !== undefined && status !== null) node.dataset.status = String(status);
  node.dataset.value = value;
}

/* ------------------------------------------------------------------ auth banner */

function ensureAuthBanner() {
  let banner = get("auth-banner");
  if (!banner) {
    const textNode = el("span", { id: "auth-text" }, "Authentication required");
    banner = el("div", { id: "auth-banner", class: "banner banner-auth", role: "alert" }, textNode);
    if (document.body) document.body.insertBefore(banner, document.body.firstChild);
  }
  return banner;
}

function renderAuthBanner() {
  const banner = get("auth-banner") || ensureAuthBanner();
  const textNode = get("auth-text");
  if (state.authRequired) {
    if (textNode) textNode.textContent = "Authentication required";
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

/* ------------------------------------------------------------------ login dialog */

function ensureLoginDialog() {
  let dialog = get("login-dialog");
  if (!dialog) {
    const overlay = el("div", {
      id: "login-overlay",
      class: "login-overlay",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": "login-title",
    });
    const card = el("div", { class: "login-card" });
    card.append(
      el("h2", { id: "login-title", text: "SignalForge API Authentication" }),
      el("p", { class: "muted", text: "Enter your API token to access the monitoring dashboard. Token is stored in session memory only and never sent outside this page." }),
      el("div", { class: "login-field" },
        el("label", { for: "login-token", text: "API Token" }),
        el("input", {
          id: "login-token",
          type: "password",
          autocomplete: "current-password",
          placeholder: "Enter token",
          class: "login-input",
        })
      ),
      el("div", { id: "login-error", class: "login-error", role: "alert", hidden: true }),
      el("div", { class: "login-actions" },
        el("button", { type: "button", class: "btn btn-primary", id: "login-submit", text: "Unlock" }),
        el("button", { type: "button", class: "btn", id: "login-cancel", text: "Cancel" })
      )
    );
    overlay.append(card);
    dialog = el("div", { id: "login-dialog", hidden: true }, overlay);
    if (document.body) document.body.appendChild(dialog);

    const input = get("login-token");
    const submit = get("login-submit");
    const cancel = get("login-cancel");
    const error = get("login-error");

    async function attemptLogin() {
      const token = (input.value || "").trim();
      if (!token) {
        error.textContent = "Please enter an API token.";
        error.hidden = false;
        return;
      }
      submit.disabled = true;
      submit.textContent = "Verifying…";
      error.hidden = true;
      try {
        const ok = await validateToken(token);
        if (ok) {
          setToken(token);
          input.value = "";
          hideLoginDialog();
          state.authRequired = false;
          state.loading = true;
          await loadDashboardData();
          startPolling();
        } else {
          clearToken();
          state.authRequired = true;
          error.textContent = "Invalid API token.";
          error.hidden = false;
        }
      } catch {
        clearToken();
        state.authRequired = true;
        error.textContent = "Could not verify token. Is the API reachable?";
        error.hidden = false;
      } finally {
        submit.disabled = false;
        submit.textContent = "Unlock";
      }
    }

    submit.addEventListener("click", attemptLogin);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") attemptLogin();
    });
    cancel.addEventListener("click", () => {
      input.value = "";
      error.hidden = true;
      if (!state.authenticated) {
        state.authRequired = true;
        state.loading = false;
        stopPolling();
        showLoginDialog();
        render();
      } else {
        hideLoginDialog();
      }
    });
  }
  return dialog;
}

function showLoginDialog() {
  state.authRequired = true;
  ensureAuthBanner();
  renderAuthBanner();
  const dialog = ensureLoginDialog();
  dialog.hidden = false;
  const input = get("login-token");
  if (input) input.focus();
  const error = get("login-error");
  if (error) error.hidden = true;
}

function hideLoginDialog() {
  const dialog = get("login-dialog");
  if (dialog) dialog.hidden = true;
}

/* ------------------------------------------------------------------ section renderers */

function dataOrNull(result) {
  return result && result.kind === "ok" && result.data && typeof result.data === "object" ? result.data : null;
}

function renderStatusBar() {
  const health = dataOrNull(state.health);
  const paper = dataOrNull(state.paperStatus);
  const q5 = dataOrNull(state.q5Collection);
  const gov = dataOrNull(state.q5Governance);
  const ai = dataOrNull(state.aiStatus);

  // API connectivity: reachable when health endpoint responds (kind === "ok").
  // Never overridden by backend overall status.
  let apiLabel = state.health && state.health.kind === "network" ? "OFFLINE"
    : (state.health && state.health.kind !== "ok") ? "UNAVAILABLE" : "HEALTHY";
  let apiStatus = apiLabel === "HEALTHY" ? "HEALTHY"
    : (apiLabel === "OFFLINE" ? "OFFLINE" : "UNAVAILABLE");
  setStatus("chip-api-connectivity", "API: " + apiLabel, apiStatus);
  setStatus("system-status-chip", apiLabel, apiStatus);

  // System health: always reflects backend overall status, never overridden by HTTP 200.
  let systemLabel = health ? text(health.status).toUpperCase()
    : (state.health && state.health.kind === "network") ? "OFFLINE"
    : (state.health && state.health.kind !== "ok") ? "UNAVAILABLE" : "N/A";
  let systemStatus = systemLabel === "HEALTHY" ? "HEALTHY"
    : (systemLabel === "DEGRADED" ? "DEGRADED"
    : (systemLabel === "UNHEALTHY" ? "UNHEALTHY"
    : (systemLabel === "OFFLINE" ? "OFFLINE"
    : (systemLabel === "UNAVAILABLE" ? "UNAVAILABLE" : "NOT_VERIFIED"))));
  setStatus("chip-system-health", "System: " + systemLabel, systemStatus);

  const paperLabel = paper && paper.available && paper.initialized ? "ENABLED" : "DISABLED";
  setStatus("chip-paper-trading", "Paper Trading: " + paperLabel, paperLabel === "ENABLED" ? "ENABLED" : "INERT");

  setStatus("chip-live-trading", "Live Trading: DISABLED", "FAILED");

  let q52Label = "N/A";
  let q52Status = "NOT_VERIFIED";
  if (state.q5Governance && (state.q5Governance.kind === "http" || state.q5Governance.kind === "error" || state.q5Governance.kind === "network")) {
    q52Label = state.q5Governance.kind === "network" ? "OFFLINE" : "UNAVAILABLE";
    q52Status = "UNAVAILABLE";
  } else if (gov && (gov.available === false || gov.blocked === true)) {
    q52Label = "BLOCKED";
    q52Status = "UNAVAILABLE";
  } else if (gov && gov.lifecycle_state === "EXPIRED") {
    q52Label = "EXPIRED";
    q52Status = "UNAVAILABLE";
  } else if (q5 && Number(q5.observation_count || 0) > 0) {
    q52Label = "COLLECTING";
    q52Status = "ACTIVE";
  } else if (q5) {
    q52Label = "IDLE";
    q52Status = "NOT_VERIFIED";
  }
  setStatus("chip-q52", "Q5.2: " + q52Label, q52Status);

  const lastUp = state.lastUpdated ? fmtIst(state.lastUpdated) : "N/A";
  const luNode = get("last-update");
  if (luNode) luNode.textContent = lastUp;

  let museLabel = "N/A";
  let museStatus = "NOT_VERIFIED";
  if (ai && ai.available && ai.valid) { museLabel = "ACTIVE"; museStatus = "ACTIVE"; }
  else if (ai && ai.available) { museLabel = "ERROR"; museStatus = "FAILED"; }
  else if (state.aiStatus && state.aiStatus.kind === "network") { museLabel = "OFFLINE"; museStatus = "OFFLINE"; }
  else if (state.aiStatus && (state.aiStatus.kind === "http" || state.aiStatus.kind === "error")) { museLabel = "UNAVAILABLE"; museStatus = "UNAVAILABLE"; }
  else if (ai) { museLabel = "UNAVAILABLE"; museStatus = "NOT_VERIFIED"; }
  setStatus("chip-meta-muse", "Meta Muse: " + museLabel, museStatus);
}

function renderQ52() {
  const collection = dataOrNull(state.q5Collection);
  const governance = dataOrNull(state.q5Governance);
  const govFailed = state.q5Governance && (state.q5Governance.kind === "http" || state.q5Governance.kind === "error" || state.q5Governance.kind === "network");
  const govBlocked = governance && (governance.available === false || governance.blocked === true);
  const govReason = govFailed ? resultReason(state.q5Governance) : (governance && governance.reason ? String(governance.reason) : (governance && governance.freeze_reason ? String(governance.freeze_reason) : ""));

  setMetric("q52-collection-id", collection ? text(collection.collection_id) : (state.q5Collection && state.q5Collection.kind !== "ok" ? resultValue(state.q5Collection) : "N/A"));
  const freezeId = (governance && governance.research_freeze_id) || (collection && (collection.associated_freeze_id || collection.freeze_id));
  setMetric("q52-freeze-id", freezeId ? text(freezeId) : (govFailed ? resultValue(state.q5Governance) : "N/A"));

  const collAuth = governance && governance.collection_authorization && typeof governance.collection_authorization === "object" ? governance.collection_authorization : null;
  const schedAuth = governance && (governance.scheduler_authorization_detail || governance.scheduler_authorization) && typeof (governance.scheduler_authorization_detail || governance.scheduler_authorization) === "object" ? (governance.scheduler_authorization_detail || governance.scheduler_authorization) : null;
  const collAuthorized = governance ? (governance.collection_authorized === true || (collAuth && collAuth.authorized === true)) : false;
  const schedAuthorized = governance ? (governance.scheduler_authorized === true || (schedAuth && schedAuth.authorized === true)) : false;

  if (govFailed) {
    setMetric("q52-authorization", "UNAVAILABLE", "UNAVAILABLE");
    setMetric("q52-scheduler", "UNAVAILABLE", "UNAVAILABLE");
  } else if (govBlocked) {
    const reason = govReason ? " (" + govReason + ")" : "";
    setMetric("q52-authorization", collAuthorized ? "AUTHORIZED" : "BLOCKED" + reason, collAuthorized ? "ACTIVE" : "UNAVAILABLE");
    setMetric("q52-scheduler", schedAuthorized ? "AUTHORIZED" : "DISABLED" + reason, schedAuthorized ? "ACTIVE" : "INERT");
  } else if (governance) {
    setMetric("q52-authorization", collAuthorized ? "AUTHORIZED" : "DISABLED", collAuthorized ? "ACTIVE" : "INERT");
    setMetric("q52-scheduler", schedAuthorized ? "AUTHORIZED" : "DISABLED", schedAuthorized ? "ACTIVE" : "INERT");
  } else {
    setMetric("q52-authorization", "N/A", "NOT_VERIFIED");
    setMetric("q52-scheduler", "N/A", "NOT_VERIFIED");
  }

  const obs = collection ? Number(collection.observation_count || 0) : null;
  setMetric("q52-observations", obs !== null ? `${obs} / ${Q5_REQUIRED_OBSERVATIONS}` : (state.q5Collection && state.q5Collection.kind !== "ok" ? resultValue(state.q5Collection) : "N/A"));
  const coverage = collection && Number.isFinite(Number(collection.coverage_fraction)) ? Number(collection.coverage_fraction) * 100 : null;
  setMetric("q52-coverage", coverage !== null ? `${coverage.toFixed(2)}% / ${Q5_REQUIRED_COVERAGE}%` : "N/A");

  const totalStored = collection && Number.isFinite(Number(collection.total_stored_observation_count)) ? Number(collection.total_stored_observation_count) : null;
  setMetric("q52-total-stored", totalStored !== null ? `${totalStored} stored` : "N/A");

  const prospectiveEligible = collection && Number.isFinite(Number(collection.prospective_eligible_observation_count)) ? Number(collection.prospective_eligible_observation_count) : null;
  setMetric("q52-prospective-eligible", prospectiveEligible !== null ? `${prospectiveEligible} / ${Q5_REQUIRED_OBSERVATIONS}` : "N/A");

  const prospectiveCoverage = collection && Number.isFinite(Number(collection.prospective_coverage_fraction)) ? Number(collection.prospective_coverage_fraction) * 100 : null;
  setMetric("q52-prospective-coverage", prospectiveCoverage !== null ? `${prospectiveCoverage.toFixed(2)}% / ${Q5_REQUIRED_COVERAGE}%` : "N/A");

  let researchReady = "N/A";
  if (collection) {
    const gov = dataOrNull(state.q5Governance);
    const ready = governance && governance.eligible_for_evaluation === true;
    researchReady = ready ? "READY" : "NOT READY";
  } else if (state.q5Collection && state.q5Collection.kind !== "ok") {
    researchReady = resultValue(state.q5Collection);
  }
  setMetric("q52-research-readiness", researchReady);

  let duration = "N/A";
  if (collection && collection.collection_started_at_utc) {
    const started = new Date(collection.collection_started_at_utc);
    if (!isNaN(started.getTime())) duration = ((Date.now() - started.getTime()) / 86400000).toFixed(1) + " days / " + Q5_REQUIRED_DURATION_DAYS + " days";
  }
  setMetric("q52-duration", duration);
  if (collection && collection.collection_started_at_utc) {
    setMetric("q52-collection-started", fmtIst(collection.collection_started_at_utc));
    try {
      const started = new Date(collection.collection_started_at_utc);
      if (!isNaN(started.getTime())) {
        const expiryMs = started.getTime() + Q5_REQUIRED_DURATION_DAYS * 86400000;
        setMetric("q52-collection-expires", fmtIst(new Date(expiryMs).toISOString()));
      } else {
        setMetric("q52-collection-expires", "N/A");
      }
    } catch {
      setMetric("q52-collection-expires", "N/A");
    }
  } else {
    setMetric("q52-collection-started", "N/A");
    setMetric("q52-collection-expires", "N/A");
  }
  const latestObs = collection ? (collection.latest_observation_utc || collection.latest_observation || "N/A") : "N/A";
  setMetric("q52-latest-observation", text(latestObs));

  let nextWindow = "N/A";
  if (collection) {
    const runtimeState = collection.timer_runtime_state || collection.runtime_timer_state || "UNKNOWN";
    const nextRun = collection.next_scheduled_run_utc;
    const sched = collection.schedule && typeof collection.schedule === "object" ? collection.schedule : null;
    if (runtimeState !== "KNOWN" || !nextRun) {
      if (sched) {
        nextWindow = "UNKNOWN (runtime unavailable; configured :08/:12 UTC hourly, Persistent=false)";
      } else {
        nextWindow = "UNKNOWN";
      }
    } else {
      nextWindow = fmtIst(nextRun);
    }
    if (collection.next_collection_window && String(collection.next_collection_window).toUpperCase() === "UNKNOWN") {
      nextWindow = "UNKNOWN";
      if (sched) nextWindow = "UNKNOWN (runtime unavailable; configured :08/:12 UTC hourly, Persistent=false)";
    }
  } else if (state.q5Collection && state.q5Collection.kind !== "ok") {
    nextWindow = resultValue(state.q5Collection);
  }
  setMetric("q52-next-window", text(nextWindow));

  if (govFailed) {
    setMetric("q52-last-result", "UNAVAILABLE: " + govReason, "UNAVAILABLE");
  } else if (collection) {
    const lastExec = collection.last_execution && typeof collection.last_execution === "object" ? collection.last_execution : null;
    if (lastExec) {
      const status = String(lastExec.status || "UNKNOWN");
      const when = lastExec.completed_at_utc ? fmtIst(lastExec.completed_at_utc) : "unknown time";
      const obs = lastExec.run_observation_count;
      const slot = lastExec.slot_utc ? " slot=" + String(lastExec.slot_utc) : "";
      const exitCode = (lastExec.exit_code !== null && lastExec.exit_code !== undefined) ? " exit=" + String(lastExec.exit_code) : "";
      const obsText = (obs !== null && obs !== undefined) ? " obs=" + String(obs) : "";
      const detail = status + " @ " + when + slot + exitCode + obsText;
      const bad = status === "DATA_INVALID" || status === "FAILED" || (lastExec.exit_code !== 0 && lastExec.exit_code !== null && lastExec.exit_code !== undefined);
      setMetric("q52-last-result", detail, bad ? "UNAVAILABLE" : "ACTIVE");
    } else if (collection.last_execution_reason) {
      setMetric("q52-last-result", "UNAVAILABLE: last_execution " + String(collection.last_execution_reason), "UNAVAILABLE");
    } else if (govBlocked) {
      const stateLabel = governance.prospective_state ? String(governance.prospective_state) : "BLOCKED";
      setMetric("q52-last-result", stateLabel + (govReason ? ": " + govReason : ""), "UNAVAILABLE");
    } else if (governance && governance.lifecycle_state === "EXPIRED") {
      setMetric("q52-last-result", "EXPIRED: " + (govReason || "collection window expired without meeting evaluation requirements"), "UNAVAILABLE");
    } else {
      setMetric("q52-last-result", text(collection.manifest_status || "N/A"), collection.ledger_valid === false ? "UNAVAILABLE" : "NOT_VERIFIED");
    }
  } else if (govBlocked) {
    const stateLabel = governance.prospective_state ? String(governance.prospective_state) : "BLOCKED";
    setMetric("q52-last-result", stateLabel + (govReason ? ": " + govReason : ""), "UNAVAILABLE");
  } else if (governance && governance.lifecycle_state === "EXPIRED") {
    setMetric("q52-last-result", "EXPIRED: " + (govReason || "collection window expired without meeting evaluation requirements"), "UNAVAILABLE");
  } else if (state.q5Collection && state.q5Collection.kind !== "ok") {
    setMetric("q52-last-result", resultValue(state.q5Collection) + ": " + resultReason(state.q5Collection), "UNAVAILABLE");
  } else {
    setMetric("q52-last-result", "N/A", "NOT_VERIFIED");
  }

  // Q5.2 validation (governance) tile
  let govVal = "N/A";
  let govValStatus = "NOT_VERIFIED";
  if (govFailed) { govVal = "UNAVAILABLE: " + govReason; govValStatus = "UNAVAILABLE"; }
  else if (govBlocked) {
    const blockedState = governance.prospective_state ? String(governance.prospective_state) : "BLOCKED";
    govVal = "UNAVAILABLE: " + (govReason || blockedState);
    govValStatus = "UNAVAILABLE";
  }
  else if (governance && governance.lifecycle_state === "EXPIRED") {
    govVal = "EXPIRED";
    govValStatus = "UNAVAILABLE";
  }
  else if (governance) { govVal = governance.eligible_for_evaluation ? "ELIGIBLE" : "NOT VALIDATED"; govValStatus = governance.eligible_for_evaluation ? "ACTIVE" : "NOT_VERIFIED"; }
  setMetric("gov-q52-validation", govVal, govValStatus);
}

function renderPipeline() {
  const collection = dataOrNull(state.q5Collection);
  const governance = dataOrNull(state.q5Governance);
  const ai = dataOrNull(state.aiStatus);
  const latest = dataOrNull(state.signalsLatest);

  const setStep = (id, label, status) => {
    const node = get(id);
    if (!node) return;
    node.textContent = label;
    node.dataset.status = status;
  };

  const govBlocked = state.q5Governance && state.q5Governance.kind !== "ok" || (governance && (governance.available === false || governance.blocked === true));
  setStep("step-market-data", "Market Data", collection && Number(collection.observation_count || 0) > 0 ? "ACTIVE" : "NOT_VERIFIED");
  setStep("step-validation", "Validation" + (govBlocked ? " (UNAVAILABLE)" : ""), govBlocked ? "UNAVAILABLE" : (governance && (governance.prospective_state === "ACCUMULATING" || governance.prospective_state === "ELIGIBLE") ? "ACTIVE" : "NOT_VERIFIED"));
  setStep("step-features", "Features", ai && ai.models > 0 ? "READY" : "NOT_VERIFIED");
  setStep("step-model", "Model", ai && ai.available && ai.valid && ai.models > 0 ? "READY" : "NOT_VERIFIED");
  setStep("step-signal", "Signal", latest && latest.signal ? "READY" : "NOT_VERIFIED");
  setStep("step-risk", "Risk", "ACTIVE");
  setStep("step-ai-explanation", "AI Explanation", ai && ai.available && ai.valid ? "ACTIVE" : "UNAVAILABLE");
  setStep("step-paper-execution", "Paper Execution", "INERT");
}

function renderPaper() {
  const status = dataOrNull(state.paperStatus);
  const account = dataOrNull(state.paperState);
  const tradesData = dataOrNull(state.paperTrades);
  const statusFailed = state.paperStatus && state.paperStatus.kind !== "ok";
  const stateFailed = state.paperState && state.paperState.kind !== "ok";

  const setP = (id, value) => setMetric(id, value);

  if (statusFailed || stateFailed) {
    const reason = statusFailed ? (resultValue(state.paperStatus) + ": " + resultReason(state.paperStatus)) : (resultValue(state.paperState) + ": " + resultReason(state.paperState));
    ["paper-starting-balance","paper-current-equity","paper-available-balance","paper-realized-pnl","paper-unrealized-pnl","paper-total-pnl","paper-return-pct","paper-max-drawdown","paper-open-positions","paper-total-trades","paper-win-rate"].forEach((id) => setP(id, "UNAVAILABLE"));
    setStatus("health-paper-trading", "UNAVAILABLE", "UNAVAILABLE");
    setMetric("gov-paper-trading", "UNAVAILABLE", "UNAVAILABLE");
    return;
  }

  const starting = account && Number.isFinite(Number(account.starting_equity)) ? Number(account.starting_equity) : (account && Number.isFinite(Number(account.initial_equity)) ? Number(account.initial_equity) : null);
  const equity = account && Number.isFinite(Number(account.equity)) ? Number(account.equity) : (status && Number.isFinite(Number(status.equity_mark)) ? Number(status.equity_mark) : null);
  const cash = account && Number.isFinite(Number(account.cash)) ? Number(account.cash) : (status && Number.isFinite(Number(status.cash)) ? Number(status.cash) : null);
  const realized = account && Number.isFinite(Number(account.realized_pnl)) ? Number(account.realized_pnl) : null;
  const unrealized = account && Number.isFinite(Number(account.unrealized_pnl)) ? Number(account.unrealized_pnl) : null;
  const total = account && Number.isFinite(Number(account.total_pnl)) ? Number(account.total_pnl) : (realized !== null || unrealized !== null ? (realized || 0) + (unrealized || 0) : null);
  const dd = account && Number.isFinite(Number(account.max_drawdown_pct)) ? Number(account.max_drawdown_pct) : null;
  const tradeCount = account && Array.isArray(account.trades_closed) ? account.trades_closed.length : (status && Number.isFinite(Number(status.trade_count)) ? Number(status.trade_count) : null);
  const hasPos = account ? !!account.position : (status ? !!status.has_open_position : false);

  setP("paper-starting-balance", starting !== null ? usd(starting) : "N/A");
  setP("paper-current-equity", equity !== null ? usd(equity) : "N/A");
  setP("paper-available-balance", cash !== null ? usd(cash) : "N/A");
  setP("paper-realized-pnl", realized !== null ? usd(realized) : "N/A");
  setP("paper-unrealized-pnl", unrealized !== null ? usd(unrealized) : "N/A");
  setP("paper-total-pnl", total !== null ? usd(total) : "N/A");
  setP("paper-return-pct", starting !== null && total !== null && starting !== 0 ? fmtPct2(total, starting) : "N/A");
  setP("paper-max-drawdown", dd !== null ? fmtPct(dd / 100) : "N/A");
  setP("paper-open-positions", account || status ? (hasPos ? "1" : "0") : "N/A");
  setP("paper-total-trades", tradeCount !== null ? String(tradeCount) : "N/A");

  const tradeList = tradesData && Array.isArray(tradesData.trades) ? tradesData.trades : (account && Array.isArray(account.trades_closed) ? account.trades_closed : []);
  let winRate = "N/A";
  if (tradeList.length) {
    const wins = tradeList.filter((t) => Number(t.net_pnl || t.pnl || 0) > 0).length;
    winRate = fmtPct2(wins, tradeList.length);
  }
  setP("paper-win-rate", winRate);

  const enabled = status && status.available && status.initialized;
  setStatus("health-paper-trading", enabled ? "ENABLED" : "DISABLED", enabled ? "ENABLED" : "INERT");
  setMetric("gov-paper-trading", enabled ? "ENABLED" : "DISABLED", enabled ? "ENABLED" : "INERT");
}

function renderPositions() {
  const body = get("positions-body");
  if (!body) return;
  clear(body);
  const account = dataOrNull(state.paperState);
  if (state.paperState && state.paperState.kind !== "ok") {
    body.append(el("tr", null, el("td", { colSpan: "8", class: "empty" }, resultValue(state.paperState) + ": " + resultReason(state.paperState))));
    return;
  }
  const position = account && account.position ? account.position : null;
  if (!position) {
    body.append(el("tr", null, el("td", { colSpan: "8", class: "empty" }, "No open paper positions.")));
    return;
  }
  body.append(el("tr", null,
    el("td", null, text(position.symbol || (account && account.symbol) || "N/A")),
    el("td", null, text(position.side)),
    el("td", null, text(position.quantity || position.qty)),
    el("td", null, usd(position.entry_price)),
    el("td", null, usd(position.current_price || position.mark_price)),
    el("td", null, text(position.stop_loss || position.risk || "N/A")),
    el("td", null, usd(position.unrealized_pnl || position.pnl)),
    el("td", null, fmtIst(position.opened_at || position.entry_ts || position.created_at || position.ts || "N/A")),
    el("td", null, text(position.status || "OPEN"))
  ));
}

function renderTradeHistory() {
  const body = get("trade-history-body");
  if (!body) return;
  clear(body);
  const tradesData = dataOrNull(state.paperTrades);
  if (state.paperTrades && state.paperTrades.kind !== "ok") {
    body.append(el("tr", null, el("td", { colSpan: "10", class: "empty" }, resultValue(state.paperTrades) + ": " + resultReason(state.paperTrades))));
    return;
  }
  const list = tradesData && Array.isArray(tradesData.trades) ? tradesData.trades : [];
  if (!list.length) {
    body.append(el("tr", null, el("td", { colSpan: "10", class: "empty" }, "No paper trades recorded.")));
    return;
  }
  for (const trade of list) {
    body.append(el("tr", null,
      el("td", null, fmtIst(trade.exit_ts || trade.entry_ts || trade.ts)),
      el("td", null, text(trade.symbol)),
      el("td", null, text(trade.side)),
      el("td", null, usd(trade.entry_price || trade.price)),
      el("td", null, usd(trade.exit_price)),
      el("td", null, text(trade.quantity || trade.qty)),
      el("td", null, usd(trade.net_pnl || trade.pnl)),
      el("td", null, text(trade.exit_reason || trade.reason || "")),
      el("td", null, text(trade.signal || trade.signal_id || "")),
      el("td", null, text(trade.ai_explanation || ""))
    ));
  }
}

function renderPnl() {
  const ledger = dataOrNull(state.paperLedger);
  const svg = get("pnl-equity-curve");
  const chip = get("pnl-range-chip");
  if (state.paperLedger && state.paperLedger.kind !== "ok") {
    if (svg) { svg.setAttribute("data-points", ""); svg.setAttribute("data-min", "N/A"); svg.setAttribute("data-max", "N/A"); }
    if (chip) { chip.textContent = resultValue(state.paperLedger) + ": " + resultReason(state.paperLedger); chip.dataset.status = "UNAVAILABLE"; }
    return;
  }
  const points = ledger && Array.isArray(ledger.equity_curve) ? ledger.equity_curve : [];
  const values = points.filter((p) => p && p.length >= 2).map((p) => Number(p[1])).filter((v) => Number.isFinite(v));
  if (!values.length) {
    if (svg) { svg.setAttribute("data-points", ""); svg.setAttribute("data-min", "N/A"); svg.setAttribute("data-max", "N/A"); }
    if (chip) { chip.textContent = "N/A"; chip.dataset.status = "NOT_VERIFIED"; }
    return;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (svg) { svg.setAttribute("data-points", String(values.length)); svg.setAttribute("data-min", String(min)); svg.setAttribute("data-max", String(max)); }
  if (chip) { chip.textContent = `${usd(min)} — ${usd(max)} (${values.length} pts)`; chip.dataset.status = "ACTIVE"; }
}

function renderSignals() {
  const body = get("signals-body");
  if (!body) return;
  clear(body);
  const history = dataOrNull(state.signals);
  if (state.signals && state.signals.kind !== "ok") {
    body.append(el("tr", null, el("td", { colSpan: "8", class: "empty" }, resultValue(state.signals) + ": " + resultReason(state.signals))));
    return;
  }
  const list = history && Array.isArray(history.signals) ? history.signals : [];
  if (!list.length) {
    body.append(el("tr", null, el("td", { colSpan: "8", class: "empty" }, "No signals recorded.")));
    return;
  }
  for (const signal of list) {
    body.append(el("tr", null,
      el("td", null, fmtIst(signal.generated_at || signal.ts || signal.timestamp_utc)),
      el("td", null, text(signal.symbol)),
      el("td", null, text(signal.direction || signal.side)),
      el("td", null, text(signal.strength ?? signal.signal_strength)),
      el("td", null, text(signal.confidence)),
      el("td", null, text((signal.decision && signal.decision.reason_codes && signal.decision.reason_codes.length > 0) ? signal.decision.reason_codes.join(", ") : (signal.risk_status || (signal.decision && signal.decision.status) || ""))),
      el("td", null, text(signal.model || signal.model_id || "")),
      el("td", null, text((signal.ai_context && signal.ai_context.summary) || signal.ai_explanation || ""))
    ));
  }
}

function renderMuse() {
  const ai = dataOrNull(state.aiStatus);
  const latest = dataOrNull(state.signalsLatest);
  if (state.aiStatus && state.aiStatus.kind !== "ok") {
    set("meta-provider", "N/A");
    set("meta-model", "N/A");
    setStatus("meta-status", resultValue(state.aiStatus) + ": " + resultReason(state.aiStatus), "UNAVAILABLE");
    set("meta-generated", "N/A");
    set("meta-latest-explanation", "N/A");
    return;
  }
  set("meta-provider", ai && ai.provider_id ? text(ai.provider_id) : "meta_muse");
  set("meta-model", ai && ai.model_id ? text(ai.model_id) : "muse-spark-1.3");
  const statusLabel = ai && ai.available && ai.valid ? "ACTIVE" : (ai && ai.available ? "ERROR" : "UNAVAILABLE");
  setStatus("meta-status", statusLabel, statusLabel === "ACTIVE" ? "ACTIVE" : (statusLabel === "ERROR" ? "FAILED" : "UNAVAILABLE"));
  set("meta-generated", latest ? fmtIst(latest.generated_at || latest.ts) : "N/A");
  set("meta-latest-explanation", latest ? text((latest.ai_context && latest.ai_context.summary) || latest.explanation || latest.ai_explanation || "") : "N/A");
}

function renderRisk() {
  const paper = dataOrNull(state.paperStatus);
  setMetric("gov-strategy-evaluation", "NOT AUTHORIZED", "NOT_AUTHORIZED");
  const paperLabel = paper && paper.available && paper.initialized ? "ENABLED" : "DISABLED";
  if (!(state.paperStatus && state.paperStatus.kind !== "ok")) setMetric("gov-paper-trading", paperLabel, paperLabel === "ENABLED" ? "ENABLED" : "INERT");
  setMetric("gov-live-trading", "DISABLED", "DISABLED");
  setMetric("gov-ai-promotion", "NOT AUTHORIZED", "NOT_AUTHORIZED");
  setMetric("gov-production-ready", "FALSE", "FALSE");
  // gov-q52-validation rendered in renderQ52 from actual governance endpoint state
}

function renderHealth() {
  const health = dataOrNull(state.health);
  const governance = dataOrNull(state.q5Governance);
  const collection = dataOrNull(state.q5Collection);
  const latest = dataOrNull(state.signalsLatest);
  const ai = dataOrNull(state.aiStatus);
  const components = health && health.checks && health.checks.components ? health.checks.components : {};

  if (state.health && state.health.kind === "network") setStatus("health-api", "OFFLINE", "OFFLINE");
  else if (state.health && state.health.kind !== "ok") setStatus("health-api", "UNAVAILABLE", "UNAVAILABLE");
  else setStatus("health-api", "HEALTHY", "HEALTHY");

  // System health: always reflects backend overall status, never overridden by HTTP 200.
  setStatus("health-system-health", health ? text(health.status).toUpperCase() : "N/A",
    health ? (health.status === "ok" ? "HEALTHY" : (health.status === "degraded" ? "DEGRADED" : (health.status === "unhealthy" ? "UNHEALTHY" : "NOT_VERIFIED"))) : "NOT_VERIFIED");

  const storage = components.storage || components.database || components.data_storage || null;
  setStatus("health-database", storage && storage.status ? String(storage.status).toUpperCase() : "N/A", storage && storage.status ? String(storage.status).toUpperCase() : "NOT_VERIFIED");

  if (state.q5Governance && state.q5Governance.kind !== "ok") {
    setStatus("health-scheduler", "UNAVAILABLE", "UNAVAILABLE");
  } else if (collection) {
    const runtime = String(collection.timer_runtime_state || collection.runtime_timer_state || "UNKNOWN").toUpperCase();
    const auth = governance ? (governance.scheduler_authorized === true || (governance.scheduler_authorization_detail && governance.scheduler_authorization_detail.authorized === true)) : false;
    if (runtime === "ENABLED" || runtime === "ACTIVE") {
      setStatus("health-scheduler", "ACTIVE", "ACTIVE");
    } else if (runtime === "DISABLED" || runtime === "INACTIVE") {
      setStatus("health-scheduler", "DISABLED", "INERT");
    } else {
      const label = auth ? "AUTHORIZED / RUNTIME UNKNOWN" : "UNKNOWN";
      setStatus("health-scheduler", label, "NOT_VERIFIED");
    }
  } else {
    setStatus("health-scheduler", "N/A", "NOT_VERIFIED");
  }

  if (state.q5Collection && state.q5Collection.kind !== "ok") {
    setStatus("health-collector", "UNAVAILABLE", "UNAVAILABLE");
  } else if (collection) {
    const proc = String(collection.collector_process_state || "UNKNOWN").toUpperCase();
    if (proc === "RUNNING" || proc === "ACTIVE") {
      setStatus("health-collector", "ACTIVE", "ACTIVE");
    } else if (proc === "STOPPED" || proc === "INACTIVE") {
      setStatus("health-collector", "INACTIVE", "INERT");
    } else {
      const evidence = String(collection.collection_evidence_state || "").toUpperCase();
      const obs = Number(collection.observation_count || 0);
      const label = evidence === "PRESENT" || obs > 0 ? "EVIDENCE PRESENT / PROCESS UNKNOWN" : "UNKNOWN";
      setStatus("health-collector", label, "NOT_VERIFIED");
    }
  } else {
    setStatus("health-collector", "N/A", "NOT_VERIFIED");
  }

  if (state.signalsLatest && state.signalsLatest.kind !== "ok") setStatus("health-signal-engine", "UNAVAILABLE", "UNAVAILABLE");
  else setStatus("health-signal-engine", latest && latest.signal ? "ACTIVE" : "N/A", latest && latest.signal ? "ACTIVE" : "NOT_VERIFIED");

  setStatus("health-risk-engine", "ACTIVE", "ACTIVE");

  if (state.aiStatus && state.aiStatus.kind !== "ok") setStatus("health-ai-provider", "UNAVAILABLE", "UNAVAILABLE");
  else setStatus("health-ai-provider", ai && ai.available && ai.valid ? "ACTIVE" : (ai && ai.available ? "ERROR" : "UNAVAILABLE"), ai && ai.available && ai.valid ? "ACTIVE" : (ai && ai.available ? "FAILED" : "UNAVAILABLE"));

  setStatus("health-docker", "N/A", "NOT_VERIFIED");
}

function renderEvents() {
  const listEl = get("event-stream");
  if (!listEl) return;
  clear(listEl);
  const eventsData = dataOrNull(state.events);
  if (state.events && state.events.kind !== "ok") {
    listEl.append(el("li", { class: "empty" }, resultValue(state.events) + ": " + resultReason(state.events)));
    return;
  }
  const items = eventsData && Array.isArray(eventsData.events) ? eventsData.events : [];
  if (!items.length) {
    listEl.append(el("li", { class: "empty" }, "No events recorded."));
    return;
  }
  for (const event of items) {
    const ts = fmtIst(event.timestamp_utc || event.ts || event.created_at);
    const desc = text(event.description || event.title || event.event_type || event.summary);
    listEl.append(el("li", null, `${ts} — ${desc}`));
  }
}

/* ------------------------------------------------------------------ render */

function render() {
  const loadingNode = get("loading-state") || get("view-root")?.querySelector(".loading-state");
  const connNode = get("connection");
  const connText = get("connection-text");

  if (state.loading) {
    if (loadingNode) loadingNode.hidden = false;
    if (connNode) connNode.dataset.state = "loading";
    if (connText) connText.textContent = "Connecting…";
    setStatus("system-status-chip", "Loading…", "NOT_VERIFIED");
    return;
  }

  if (loadingNode) loadingNode.hidden = true;
  if (connNode) connNode.dataset.state = state.authenticated ? "connected" : "unauthenticated";
  if (connText) connText.textContent = state.authenticated ? "Connected" : "Not authenticated";

  renderAuthBanner();
  const safe = (fn) => { try { fn(); } catch { /* isolate panel render failures */ } };
  safe(renderStatusBar);
  safe(renderQ52);
  safe(renderPipeline);
  safe(renderPaper);
  safe(renderPositions);
  safe(renderTradeHistory);
  safe(renderPnl);
  safe(renderSignals);
  safe(renderMuse);
  safe(renderRisk);
  safe(renderHealth);
  safe(renderEvents);
}

/* ------------------------------------------------------------------ refresh */

async function loadDashboardData() {
  const [
    health,
    paperStatus,
    paperState,
    paperTrades,
    paperLedger,
    signalsLatest,
    signals,
    q5Collection,
    q5Governance,
    aiStatus,
    aiMetrics,
    audit,
    events,
  ] = await Promise.all([
    fetchResult(API.health),
    fetchResult(API.paperStatus),
    fetchResult(API.paperState),
    fetchResult(API.paperTrades),
    fetchResult(API.paperLedger),
    fetchResult(API.signalsLatest),
    fetchResult(API.signals),
    fetchResult(API.q5Collection),
    fetchResult(API.q5Governance),
    fetchResult(API.aiStatus),
    fetchResult(API.aiMetrics),
    fetchResult(API.audit),
    fetchResult(API.events),
  ]);

  const results = [
    health, paperStatus, paperState, paperTrades, paperLedger,
    signalsLatest, signals, q5Collection, q5Governance,
    aiStatus, aiMetrics, audit, events,
  ];

  if (results.some((result) => result.kind === "auth")) {
    clearToken();
    stopPolling();
    state.authRequired = true;
    state.loading = false;
    hideLoginDialog();
    showLoginDialog();
    render();
    return false;
  }

  state.health = health;
  state.paperStatus = paperStatus;
  state.paperState = paperState;
  state.paperTrades = paperTrades;
  state.paperLedger = paperLedger;
  state.signalsLatest = signalsLatest;
  state.signals = signals;
  state.q5Collection = q5Collection;
  state.q5Governance = q5Governance;
  state.aiStatus = aiStatus;
  state.aiMetrics = aiMetrics;
  state.audit = audit;
  state.events = events;
  state.authRequired = false;
  state.loading = false;
  state.lastUpdated = new Date().toISOString();
  render();
  return true;
}

async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    if (!state.authenticated) {
      stopPolling();
      state.authRequired = true;
      state.loading = false;
      showLoginDialog();
      render();
      return;
    }
    await loadDashboardData();
  } catch {
    state.loading = false;
    render();
  } finally {
    state.refreshing = false;
  }
}

function startPolling() {
  if (state.timer) return;
  state.timer = setInterval(refresh, POLL_MS);
}

async function init() {
  ensureAuthBanner();
  const stored = getToken();
  if (!stored) {
    state.authRequired = true;
    state.loading = false;
    showLoginDialog();
    render();
    return;
  }

  state.authRequired = false;
  const ok = await validateToken(stored);
  if (ok) {
    setToken(stored);
    await loadDashboardData();
    startPolling();
  } else {
    clearToken();
    state.authRequired = true;
    state.loading = false;
    showLoginDialog();
    render();
  }
}

function stopPolling() {
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
  }
}

if (typeof document !== "undefined" && document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else if (typeof document !== "undefined") {
  init();
}

