---
name: verification-and-release
description: Run factual checks and prepare reproducible SignalForge handoffs
---

# Verification and Release

## Procedure

1. Read `AGENTS.md`; run the actual checks for the approved phase. Phase 1 minimum:
   `uv sync`, `uv run sf health`, `uv run sf config-show`, `uv run pytest`,
   `uv run ruff check .`.
2. Record exact commands and output status; separate failures, skips, limitations.
3. Verify agent/skill discovery through the supported engine where relevant.
4. Never claim unrun work; never count skips as passes.

## Validation standards

- Pass/fail/skip counts reported per command; new vs pre-existing failures split.
- Release verdict (go/no-go) tied to the phase gate, with blockers listed.

## Failure handling

- Failures → command, output, suspected cause, verdict. Blocked checks → marked
  blocked with reason, never passed.

## Completion requirements

Handoff: implemented files, exact test record, limitations, unresolved risks,
next approval gate — reproducible by the lead.
