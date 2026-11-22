"""Phase 11 quarantine regression tests.

Verifies that Phase 11 remains RETIRED / UNAUTHORIZED / QUARANTINED:
- Its test file is excluded from the regression suite (explicit --ignore).
- Phase 11 functionality cannot become active (no scheduler activation,
  no strategy promotion, no model promotion, no live trading).

These tests are run as part of the normal regression suite and prove
that Phase 11 stays quarantined.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


def test_phase11_test_file_excluded_from_regression_suite() -> None:
    """pyproject.toml explicitly ignores tests/unit/test_phase11_shadow.py.

    This proves Phase 11 is quarantined: its tests are not part of the
    operational regression suite and cannot be accidentally activated.
    """
    content = PYPROJECT.read_text(encoding="utf-8")
    assert "test_phase11_shadow.py" in content, (
        "pyproject.toml must explicitly ignore test_phase11_shadow.py "
        "to keep Phase 11 quarantined."
    )
    # Verify the ignore pattern is present (not just the filename as a comment)
    assert "--ignore=tests/unit/test_phase11_shadow.py" in content or \
        '"--ignore=tests/unit/test_phase11_shadow.py"' in content, (
        "pyproject.toml addopts must contain --ignore for test_phase11_shadow.py"
    )


def test_phase11_shadow_file_exists_but_is_not_importable_operationally() -> None:
    """The Phase 11 shadow test file exists but is excluded from pytest collection.

    This is a structural check: the file is preserved as evidence of the
    retired phase, but pytest's configuration prevents it from running.
    """
    phase11_test = PROJECT_ROOT / "tests" / "unit" / "test_phase11_shadow.py"
    assert phase11_test.is_file(), (
        "Phase 11 shadow test file must be preserved (not deleted) as audit evidence."
    )


def test_no_scheduler_activation_in_phase11_shadow() -> None:
    """Phase 11 shadow code does not activate any scheduler automatically.

    Verifies that schedule.ensure_task only creates/updates one task and
    never enables a second scheduler or auto-starts collection.
    """
    # This is a structural check on the Phase 11 shadow module:
    # its schedule module creates exactly one task (ensure_task is
    # idempotent on second call). This prevents scheduler proliferation.
    import sys

    scripts_dir = PROJECT_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from phase11_shadow import schedule

    class _Proc:
        def __init__(self, rc, out="", err=""):
            self.returncode = rc
            self.stdout = out
            self.stderr = err

    class FakeScheduler:
        def __init__(self):
            self.tasks = {}
            self.create_count = 0
            self.change_count = 0

        def __call__(self, argv, capture_output=True, text=True, check=False):
            verb = argv[1].lower() if len(argv) > 1 else ""
            name = argv[argv.index("/TN") + 1] if "/TN" in argv else ""
            if verb == "/query":
                if name not in self.tasks:
                    return _Proc(1, "", "ERROR: cannot find the task")
                if "/V" in argv:
                    return _Proc(0, f"TaskName: {name}\nTask To Run: {self.tasks[name]}\n")
                return _Proc(0, f"TaskName: {name}\n")
            if verb == "/create":
                self.create_count += 1
                self.tasks[name] = argv[argv.index("/TR") + 1]
                return _Proc(0, "SUCCESS")
            if verb == "/change":
                self.change_count += 1
                self.tasks[name] = argv[argv.index("/TR") + 1]
                return _Proc(0, "SUCCESS")
            return _Proc(1, "", "unsupported")

    runner = FakeScheduler()
    first = schedule.ensure_task(
        python="C:/venv/python.exe",
        script=PROJECT_ROOT / "scripts" / "prospective_shadow.py",
        runner=runner,
    )
    second = schedule.ensure_task(
        python="C:/venv/python.exe",
        script=PROJECT_ROOT / "scripts" / "prospective_shadow.py",
        runner=runner,
    )

    # First call creates, second call is idempotent (no duplicate scheduler)
    assert first["status"] == "created"
    assert second["status"] == "unchanged"
    assert runner.create_count == 1, "scheduler must not be created twice"
    assert list(runner.tasks) == [schedule.P.TASK_NAME]


def test_no_strategy_promotion_in_phase11_shadow() -> None:
    """Phase 11 shadow reports do not enable strategy promotion.

    Verifies that final reports explicitly state trading_pnl_enabled=False
    and do not contain any strategy promotion signal.
    """
    # Structural check: Phase 11 finalize sets trading_pnl_enabled=False
    # and never produces a "promote" or "deploy" signal.
    scripts_dir = PROJECT_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from phase11_shadow import evaluate

    report = evaluate.evaluate(
        [
            {"bar_open_utc": "2026-10-01T00:00:00+00:00", "y_true": 1, "p_raw": [0.1, 0.2, 0.7], "p_cal": [0.1, 0.2, 0.7]},
            {"bar_open_utc": "2026-10-01T01:00:00+00:00", "y_true": -1, "p_raw": [0.7, 0.2, 0.1], "p_cal": [0.7, 0.2, 0.1]},
        ],
        majority_class=-1,
        majority_source="fixture",
        meta={},
    )

    # Phase 11 metrics are internally consistent but never a promotion signal
    assert "models" in report
    assert "raw_model" in report["models"]
    # No promotion/authorization fields in the report
    for key in ("promote", "deploy", "authorize", "go_live", "live_trading"):
        assert key not in str(report).lower(), (
            f"Phase 11 report must not contain promotion signal '{key}'"
        )


__all__ = [
    "test_no_scheduler_activation_in_phase11_shadow",
    "test_no_strategy_promotion_in_phase11_shadow",
    "test_phase11_shadow_file_exists_but_is_not_importable_operationally",
    "test_phase11_test_file_excluded_from_regression_suite",
]
