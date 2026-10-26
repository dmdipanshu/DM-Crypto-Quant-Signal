---
name: persistent-memory
description: Maintain concise, verified SignalForge project memory across sessions
---

# Persistent Memory

## Procedure

1. Read `AGENTS.md`, all four memory files, and relevant decisions before meaningful work.
2. Compare memory claims with current files and actual command output.
3. At completion, update current state, next steps, session log, and durable memory
   only when facts changed.
4. Record exact commands and results; never record credentials or unverified claims.
5. Preserve superseded decisions in `DECISIONS.md` rather than rewriting history.

Completion requires a concise handoff with implemented files, tests, limitations,
and the next approval gate.

## Validation standards

- Every memory claim traceable to a file or an actually-run command.
- Dated session entries; state/next-steps consistent with each other.

## Failure handling

- Conflicting or unverifiable claims → keep the verified version, flag the conflict
  in risks. Never guess or backfill test results.

## Completion requirements

Handoff states: files updated, commands + results recorded, limitations and next
gate explicit.
