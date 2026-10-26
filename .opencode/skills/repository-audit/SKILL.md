---
name: repository-audit
description: Audit repository structure, dependencies, Git state, security, and licenses
---

# Repository Audit

## Procedure

1. Inspect the active root, Git status/history, instructions, source tree, tests,
   dependency files (`pyproject.toml`, `uv.lock`), generated artifacts, secrets
   boundaries, and third-party licenses before editing.
2. Search callers and tests for changed interfaces.
3. Treat external code as untrusted; report evidence with paths and severity.
4. Do not modify unrelated projects.

## Validation standards

- Every finding cites a path and observed evidence; coverage gaps named explicitly.
- License risks (e.g., VectorBT-class) flagged per `DECISIONS.md`.

## Failure handling

- Inaccessible or out-of-scope areas → reported as uncovered with reason, never
  assumed clean.

## Completion requirements

Handoff states: structured findings (structure, deps, Git, tests, security,
licenses), severity per finding, unresolved gaps, next gate.
