"""Server-rendered dashboard pages (Phase 7).

Design rules (tested):

* Same repo readers as the API (shared adapters, no second source of truth).
* Every dynamic value goes through :func:`html.escape`; external/model text
  is never rendered as raw HTML and no model-generated JavaScript exists.
* No live updating, no polling, no trading buttons, no order forms.
* Empty states are explicit; stale/expired/rejected records are labeled;
  raw scores are never shown as calibrated probabilities.
* Paper figures are always labeled hypothetical; uninitialized paper state
  is unmistakable, never a healthy portfolio.
"""

from __future__ import annotations

import html
from typing import Any

APP_NAME = "SignalForge"
NAV = (
    ("overview", "Overview", "/dashboard/"),
    ("progress", "Progress", "/dashboard/progress/"),
    ("signals", "Signals", "/dashboard/signals"),
    ("research", "Research", "/dashboard/research"),
    ("backtests", "Backtests", "/dashboard/backtests"),
    ("paper", "Paper", "/dashboard/paper"),
    ("models", "Models", "/dashboard/models"),
)

_CSS = """
:root{color-scheme:light dark}
body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:0 auto;max-width:1080px;padding:16px;line-height:1.45}
nav{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0;padding:8px 0;border-top:1px solid #888;border-bottom:1px solid #888}
nav a{text-decoration:none;font-weight:600}
nav a.active{text-decoration:underline}
table{border-collapse:collapse;width:100%;margin:12px 0}
th,td{border:1px solid #888;padding:6px 8px;text-align:left;font-size:14px}
th{background:rgba(127,127,127,.15)}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;border:1px solid #888;font-size:12px}
.ok{border-color:green}.warn{border-color:orange}.bad{border-color:red}.muted{opacity:.75}
.card{border:1px solid #888;border-radius:8px;padding:12px;margin:12px 0}
.small{font-size:12px}.mono{font-family:ui-monospace,monospace;font-size:12px}
footer{margin-top:24px;font-size:12px;opacity:.75}
"""


def _esc(value: Any) -> str:
    if value is None:
        return "<span class='muted'>n/a</span>"
    return html.escape(str(value), quote=True)


def _badge(text: str, kind: str = "") -> str:
    return f"<span class='badge {html.escape(kind)}'>{html.escape(text)}</span>"


def _direction_badge(direction: str) -> str:
    kinds = {"LONG": "ok", "SHORT": "warn", "NEUTRAL": "", "NO_TRADE": "muted"}
    return _badge(direction, kinds.get(direction, "bad"))


def _status_badge(status: str) -> str:
    kinds = {"approved": "ok", "abstained": "warn", "rejected": "bad", "ok": "ok"}
    return _badge(status, kinds.get(status, ""))


def layout(active: str, title: str, body: str) -> str:
    """Full page shell with nav (all dynamic parts pre-escaped by callers)."""
    links = "".join(
        f"<a href='{html.escape(href)}' class='{'active' if key == active else ''}'>{html.escape(label)}</a>"
        for key, label, href in NAV
    )
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)} — {html.escape(APP_NAME)}</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>{html.escape(APP_NAME)}</h1>"
        f"<p class='small muted'>Read-only research dashboard. Advisory only — not trading advice, no orders.</p>"
        f"<nav>{links}</nav>{body}"
        "<footer>All timestamps UTC. Historical data is labeled as such; nothing here is a live recommendation.</footer>"
        "</body></html>"
    )


def _empty(what: str, hint: str = "") -> str:
    extra = f" {_esc(hint)}" if hint else ""
    return f"<div class='card'><p>No {html.escape(what)} recorded.{extra}</p></div>"


def overview(ctx: dict[str, Any]) -> str:
    """Overview page from a pre-fetched context dict (keys documented below)."""
    rows: list[str] = []

    def _row(label: str, value_html: str) -> None:
        rows.append(f"<tr><th>{html.escape(label)}</th><td>{value_html}</td></tr>")

    health = ctx.get("health", {})
    _row(
        "Service",
        _status_badge(str(health.get("status", "unknown")))
        + f" <span class='small mono'>{_esc(health.get('package_version', ''))}</span>",
    )
    data = ctx.get("data_status", {})
    if data.get("available") and int(data.get("total", 0) or 0) > 0:
        _row("Datasets", f"{_esc(data.get('total', 0))} snapshot(s) recorded")
    else:
        _row("Datasets", _badge("none recorded", "warn"))
    latest = ctx.get("latest_signal")
    if latest and latest.get("available") and latest.get("signal"):
        sig = latest["signal"]
        _row(
            "Latest signal",
            f"{_direction_badge(str(sig.get('direction', '?')))} {_esc(sig.get('symbol'))} "
            f"{_esc(sig.get('timeframe'))} <span class='small'>{_esc(sig.get('generated_at'))} UTC</span>",
        )
    else:
        _row("Latest signal", _badge("none recorded", "warn"))
    models = ctx.get("models_status", {})
    _row(
        "Models",
        _esc(models.get("count", 0)) + " registered"
        if models.get("available")
        else _badge("registry unavailable", "bad"),
    )
    backtests = ctx.get("backtests", {})
    _row(
        "Backtests",
        _esc(backtests.get("total", 0)) + " run(s)"
        if backtests.get("available")
        else _badge("none recorded", "warn"),
    )
    paper = ctx.get("paper", {})
    if paper.get("available"):
        _row(
            "Paper sandbox",
            _status_badge(str(paper.get("status", "?")))
            + " <span class='small'>(hypothetical figures)</span>",
        )
    else:
        _row("Paper sandbox", _badge("uninitialized — not a portfolio", "warn"))
    audit = ctx.get("audit", {})
    _row("Audit chain", _status_badge("ok") if audit.get("ok") else _badge("unverified", "bad"))
    ai = ctx.get("ai_status", {})
    if ai.get("available"):
        ai_state = "enabled" if ai.get("enabled") else "disabled"
        ai_valid = "valid" if ai.get("valid") else "invalid"
        error = ai.get("error")
        if error:
            _row(
                "AI gateway",
                _badge(f"{ai_state} · {ai_valid} · error: {error.get('code', 'unknown')}", "bad"),
            )
        else:
            _row(
                "AI gateway",
                f"{_badge(ai_state, 'ok')} · {_badge(ai_valid)} · "
                f"{ai.get('providers')} providers, {ai.get('models')} models, "
                f"{ai.get('routes')} routes",
            )
    else:
        _row("AI gateway", _badge("disabled — advisory only", "muted"))
    ai_metrics = ctx.get("ai_metrics", {})
    if ai_metrics.get("available"):
        _row("AI requests", str(ai_metrics.get("total_requests", 0)))
        _row("AI success rate", f"{ai_metrics.get('success_percentage', 0)}%")
        _row("AI avg latency (ms)", str(ai_metrics.get("average_latency_ms", 0)))
        _row("AI total tokens", str(ai_metrics.get("total_tokens", 0)))
        _row("AI invalid outputs", str(ai_metrics.get("invalid_output_count", 0)))
    body = f"<h2>Overview</h2><table>{''.join(rows)}</table>"
    body += "<p class='small'>A healthy service does not mean a model is trained or a strategy is profitable.</p>"
    return layout("overview", "Overview", body)


def signals_page(signals: list[dict[str, Any]], total: int) -> str:
    """Signal records table (never shows raw scores as calibrated)."""
    if not signals:
        return layout(
            "signals",
            "Signals",
            "<h2>Signals</h2>" + _empty("signals", "Run `sf signal generate`."),
        )
    rows = []
    for sig in signals:
        decision = sig.get("decision", {}) if isinstance(sig.get("decision"), dict) else {}
        pred = sig.get("prediction", {}) if isinstance(sig.get("prediction"), dict) else {}
        cal = pred.get("calibration_status", "unavailable")
        cal_label = "calibrated" if cal == "calibrated" else f"uncalibrated ({cal})"
        reasons = ", ".join(str(r) for r in decision.get("reason_codes", []))
        ai_ctx = sig.get("ai_context", {}) if isinstance(sig.get("ai_context"), dict) else {}
        ai_summary = ai_ctx.get("summary", "")
        ai_status = ai_ctx.get("status", "disabled")
        ai_provider = ai_ctx.get("provider_id", "")
        ai_model = ai_ctx.get("model_id", "")
        ai_html = ""
        if ai_status == "ok" and ai_summary:
            ai_html = (
                f"<td class='small mono'><b>AI ({ai_provider}/{ai_model}):</b> "
                f"{_esc(ai_summary[:200])}" + ("..." if len(ai_summary) > 200 else "") + "</td>"
            )
        else:
            ai_html = f"<td class='small muted'>AI: {ai_status}</td>"
        rows.append(
            "<tr>"
            f"<td class='mono'>{_esc(sig.get('signal_id'))}</td>"
            f"<td>{_esc(sig.get('symbol'))}<br><span class='small'>{_esc(sig.get('timeframe'))}</span></td>"
            f"<td>{_direction_badge(str(sig.get('direction')))}</td>"
            f"<td>{_status_badge(str(decision.get('status')))}</td>"
            f"<td class='small'>{_esc(sig.get('generated_at'))}<br>exp {_esc(sig.get('valid_until'))}</td>"
            f"<td class='small'>{(cal_label)}</td>"
            f"<td>{_esc(sig.get('data_quality'))}</td>"
            f"<td class='small mono'>{_esc(reasons)}</td>"
            f"{ai_html}"
            "</tr>"
        )
    body = (
        "<h2>Signals</h2>"
        f"<p class='small'>Showing {len(signals)} of {total} recorded (newest first). Historical records — not live recommendations.</p>"
        "<table><tr><th>ID</th><th>Symbol</th><th>Direction</th><th>Decision</th>"
        "<th>Generated / expiry (UTC)</th><th>Calibration</th><th>Data</th><th>Reasons</th>"
        "<th>AI Explanation</th></tr>" + "".join(rows) + "</table>"
    )
    return layout("signals", "Signals", body)


def research_page(summary: dict[str, Any], experiments: list[dict[str, Any]]) -> str:
    """Recorded evaluation results (research metrics, never merged into one score)."""
    parts = ["<h2>Quant research</h2>"]
    if not summary.get("experiments_available"):
        parts.append(_empty("recorded experiments", "Run walk-forward validation first."))
    else:
        parts.append(
            f"<div class='card'><p>Recorded experiments: <b>{_esc(summary.get('experiment_count'))}</b> · "
            f"recorded trials: <b>{_esc(summary.get('recorded_trials'))}</b></p>"
            f"<p class='small'>By status: {_esc(summary.get('experiments_by_status'))}</p>"
            f"<p class='small'>{_esc(summary.get('note', ''))}</p></div>"
        )
    holdout = summary.get("holdout", {})
    if holdout.get("available"):
        state = (
            "pristine (never evaluated)"
            if holdout.get("pristine")
            else "USED — no longer an untouched holdout"
        )
        parts.append(f"<div class='card'><p>Holdout ledger: <b>{html.escape(state)}</b></p></div>")
    if experiments:
        rows = "".join(
            f"<tr><td class='mono'>{_esc(e.get('experiment_id'))}</td><td>{_esc(e.get('status'))}</td>"
            f"<td class='small'>{_esc(e.get('protocol', ''))}</td></tr>"
            for e in experiments
        )
        parts.append(
            f"<table><tr><th>Experiment</th><th>Status</th><th>Protocol</th></tr>{rows}</table>"
        )
    parts.append(
        "<p class='small'>Research proxies are labeled as such; directional, trade-level, and portfolio-level metrics are never combined.</p>"
    )
    return layout("research", "Research", "".join(parts))


def backtests_page(runs: list[dict[str, Any]], total: int) -> str:
    """Persisted backtest runs (missing runs shown as unavailable, never faked)."""
    if not runs:
        return layout(
            "backtests",
            "Backtests",
            "<h2>Backtests</h2>"
            + _empty(
                "backtest runs", "Create one with `sf backtest run --snapshot <dir> --model <dir>`."
            ),
        )
    rows = []
    for run in runs:
        if not run.get("available"):
            rows.append(
                f"<tr><td class='mono'>{_esc(run.get('run_id'))}</td><td colspan='5'>{_badge('unavailable: ' + str(run.get('reason', 'corrupt')), 'bad')}</td></tr>"
            )
            continue
        rows.append(
            f"<tr><td class='mono'>{_esc(run.get('run_id'))}</td>"
            f"<td>{_esc(run.get('symbol'))} {_esc(run.get('timeframe'))}</td>"
            f"<td>{_esc(run.get('trade_count'))}</td>"
            f"<td>{_esc(run.get('net_pnl'))}</td>"
            f"<td class='small'>{_esc(run.get('warnings') or [])}</td></tr>"
        )
    body = (
        "<h2>Backtests</h2>"
        f"<p class='small'>Showing {len(runs)} of {total} persisted run(s). Historical simulations — not live performance.</p>"
        "<table><tr><th>Run</th><th>Market</th><th>Trades</th><th>Net P&amp;L</th><th>Warnings</th></tr>"
        + "".join(rows)
        + "</table>"
        "<p class='small'>The dashboard never starts a backtest; use `sf backtest run`.</p>"
    )
    return layout("backtests", "Backtests", body)


def paper_page(account: dict[str, Any] | None, state: str) -> str:
    """Paper sandbox (hypothetical figures; uninitialized is unmistakable)."""
    if account is None:
        return layout(
            "paper",
            "Paper",
            "<h2>Paper trading</h2>"
            "<div class='card'><p><b>Paper sandbox uninitialized — this is not a portfolio.</b></p>"
            f"<p class='small'>State: {_esc(state)}</p>"
            "<p class='small'>Paper trading is hypothetical and advisory-only. The dashboard has no start/stop/flatten/order controls.</p></div>",
        )
    pos = account.get("position")
    pos_html = "<span class='muted'>flat (no open position)</span>"
    if pos:
        pos_html = (
            f"{_direction_badge('LONG' if str(pos.get('side', '')).upper() == 'LONG' else 'SHORT')} "
            f"{_esc(pos.get('qty'))} @ {_esc(pos.get('entry_price'))} "
            f"<span class='small'>since {_esc(pos.get('entry_ts'))}</span>"
        )
    rows = "".join(
        f"<tr><td class='mono'>{_esc(t.get('trade_id'))}</td><td>{_esc(t.get('exit_reason'))}</td>"
        f"<td>{_esc(t.get('net_pnl'))}</td></tr>"
        for t in (account.get("trades_closed", []) or [])[-50:]
    )
    body = (
        "<h2>Paper trading <span class='small muted'>(hypothetical — not real money)</span></h2>"
        f"<div class='card'><p>Status: {_status_badge(str(account.get('status')))} · "
        f"Cash: {_esc(account.get('cash'))} · Marked equity: {_esc(account.get('equity_mark'))} "
        f"<span class='small'>({_esc(account.get('equity_mark_ts'))} UTC)</span></p>"
        f"<p>Open position: {pos_html}</p>"
        f"<p class='small'>Closed paper trades: {_esc(account.get('trade_counter'))}</p></div>"
        f"<table><tr><th>Trade</th><th>Exit</th><th>Net P&amp;L</th></tr>{rows or '<tr><td colspan=3>none closed yet</td></tr>'}</table>"
    )
    return layout("paper", "Paper", body)


def models_page(models: list[dict[str, Any]]) -> str:
    """Registry metadata + provider/dependency status (no credentials)."""
    if not models:
        rows = "<tr><td colspan='5'>No models registered.</td></tr>"
    else:
        rows = "".join(
            f"<tr><td class='mono'>{_esc(m.get('model_id'))}</td><td>{_esc(m.get('family'))}</td>"
            f"<td>{_esc(m.get('version'))}</td><td>{_status_badge(str(m.get('status')))}</td>"
            f"<td class='small'>{_esc((m.get('limitations') or '')[:200])}</td></tr>"
            for m in models
        )
    return layout(
        "models",
        "Models",
        "<h2>Models &amp; system</h2>"
        f"<table><tr><th>Model</th><th>Family</th><th>Version</th><th>Status</th><th>Limitations</th></tr>{rows}</table>"
        "<p class='small'>Artifacts are checksummed at load; an adapter existing does not mean a provider connection works. No credentials are ever displayed.</p>",
    )


__all__ = [
    "APP_NAME",
    "NAV",
    "backtests_page",
    "layout",
    "models_page",
    "overview",
    "paper_page",
    "research_page",
    "signals_page",
]
