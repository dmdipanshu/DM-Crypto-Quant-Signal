---
name: multi-agent-orchestration
description: Divide SignalForge work safely among project-local agents
---

# Multi-Agent Orchestration

## Procedure

1. Confirm the approved phase and read project memory before delegating.
2. Divide work by bounded ownership; never assign overlapping edits.
3. Use parallel agents only for genuinely independent tasks.
4. Require each agent to return findings, files, tests, and unresolved risks.
5. Review all agent output against current source before integration.
6. Run integrated checks after applying changes and report which agents actually ran.

Agent suggestions are not verification. The lead owns the final decision.

## Validation standards

- Ownership map checked for overlaps before launch; agent reports verified against
  files, not taken on trust; integrated test suite run post-merge.

## Failure handling

- Conflicting reports → re-inspect source, decide explicitly with rationale.
- Agent no-show or partial output → record as not-run, reassign or narrow scope.

## Completion requirements

Handoff states: which agents ran, files changed, integrated checks with exact
commands + counts, unresolved risks, next approval gate.
