# CI Secret Scanning

## Purpose and scope

This document describes the secret scanning infrastructure used to prevent
committed secrets from entering the repository. It covers:

- Local scanning with scripts/security_scan.py
- CI integration via GitHub Actions
- Pattern coverage, false positive handling, and remediation

The scanner inspects tracked repo files (.py, .toml, .env, .md, .json,
.yaml, .yml) and reports file paths and line numbers only — never the secret
values themselves.

## How to run locally

`powershell
uv run python scripts/security_scan.py
`

Scope specific paths:

`powershell
uv run python scripts/security_scan.py scripts docs
`

## Exit code semantics

| Exit code | Meaning |
|-----------|---------|
| 0 | No secrets detected (clean) |
| 1 | Confirmed secrets found |
| 2 | Scanner error |

In CI, a non-zero exit code fails the job. Treat exit code 1 as a blocker:
rotate the exposed secret and remove it from the code before merging.

## Patterns detected

The scanner flags lines matching any of the following (values are never printed):

- sk- prefixed API keys (e.g. Stripe/OpenAI-style keys)
- Bearer <token> authorization headers
- pi_key= / pi-key= key-value assignments
- -----BEGIN ... PRIVATE KEY----- private key blocks
- gh[pousr]_ GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)

## False positive handling

The following are intentionally not flagged:

- Comment lines (lines starting with #)
- Safe placeholder lines matching META_MUSE_API_KEY=
- Test fixtures containing the sk-test- prefix marker

If a legitimate non-secret triggers a detection, add an exclusion to the scanner
rather than disabling scanning. Do not add secret values to the code to "satisfy"
the scanner.

## CI integration

GitHub Actions example (.github/workflows/security-scan.yml):

`yaml
name: Security Scan

on:
  push:
    branches: [master]
  pull_request:
    branches: [master]

jobs:
  security-scan:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install uv
        run: |
          curl -LsSf https://astral.sh/uv/install.sh | sh
          echo "C:\Users\dmcom/.local/bin" >> 

      - name: Run secret scanner
        run: uv run python scripts/security_scan.py
`

The scanner step fails with exit code 1 when secrets are detected. No secret
values are printed in CI logs.

## What to do when a secret is detected

1. Treat the exposed secret as compromised.
2. Rotate the secret through the provider (revoke/regenerate the token or key).
3. Remove the secret value from the code; replace it with a reference to an
   environment variable or a safe placeholder (e.g. sk-test- for test fixtures,
   or META_MUSE_API_KEY= for documented placeholders).
4. Re-run uv run python scripts/security_scan.py locally to confirm exit code 0
   before re-submitting.
5. Never print secret values in CI logs or in the scanner output.