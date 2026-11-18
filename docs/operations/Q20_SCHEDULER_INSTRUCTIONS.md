# Q20 Prospective Collection Scheduler â€” Task Scheduler Instructions

**Status:** Registered and enabled. Q29 registered `SignalForge-Q20-Prospective-Collection` under `dm\dmcom` (non-elevated), every-minute trigger, UTC HH:08 primary / HH:12 retry windows enforced in-script.

---

## Task Name

`SignalForge-Q20-Prospective-Collection`

---

## Exact Registration Command (schtasks)

Run in an elevated PowerShell or Command Prompt from the project root:

```powershell
schtasks /Create /TN "SignalForge-Q20-Prospective-Collection" `
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"D:\DM-Crypto-Quant-Signal\scripts\q20_prospective_scheduler.ps1`"" `
  /SC MINUTE /MO 1 `
  /RU "dm\dmcom" `
  /F
```

- `/SC MINUTE /MO 1` â€” trigger every minute; the script itself only acts at UTC minute 08 (primary) and minute 12 (retry), so off-minute invocations exit 0 as a no-op.
- `/RU`— execution identity; Q29 used non-elevated user context `dm\dmcom` (no SYSTEM, no /RL HIGHEST).
- `/RL HIGHEST`— omitted; Q29 runs non-elevated as `dm\dmcom`.
- `/F` â€” force create, replacing any existing task with the same name.

---

## Exact Removal Command

```powershell
schtasks /Delete /TN "SignalForge-Q20-Prospective-Collection" /F
```

---

## Execution Identity Requirements

- Recommended identity: `NT AUTHORITY\SYSTEM` (no interactive logon, no password prompt).
- Alternative: a dedicated service account with read access to the project directory, write access to `%TEMP%` (lock + logs), and execute rights on `powershell.exe`, `uv.exe`, and `python.exe`.
- Do **not** use an interactive user account whose password may expire; task will fail silently on password change.
- The account must have `Log on as a batch job` right.

---

## Working Directory

`D:\DM-Crypto-Quant-Signal`

The script resolves `$scriptRoot` from its own location and runs `uv run python scripts/run_q5_prospective_collection.py --once` from there. Ensure `uv` and `python` are resolvable on `PATH` for the scheduled identity, or supply absolute paths.

---

## Python / uv Executable Path

Default resolution (relies on `PATH` for the scheduled identity):

- `uv` â†’ `uv`
- `uv run python` â†’ uses the project `.venv` Python

If `PATH` is not reliable for the scheduled identity, replace `$uvExe = "uv"` in the script with the absolute path, e.g.:

```powershell
$uvExe = "C:\Users\<account>\.local\bin\uv.exe"
```

and confirm `python` inside the venv resolves via `uv run`.

---

## Log Location

- Main log: `$env:TEMP\signalforge_prospective_collect.log`
- Error log: `$env:TEMP\signalforge_prospective_collect_errors.log`

For `NT AUTHORITY\SYSTEM`, `%TEMP%` typically resolves to `C:\Windows\Temp`. Both logs are appended (UTF-8) and contain no credentials or secrets.

---

## Expected Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success / no-op (wrong minute or candle already collected) |
| 1 | Collection error (underlying collector failed) |
| 2 | Lock held by another process (no overlapping runs) |
| 3 | Script error (unhandled exception) |

---

## Verification Commands

1. Dry-run (does not register a task, no collection runs):
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/q20_prospective_scheduler.ps1 -DryRun
   ```

2. Manual primary test:
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/q20_prospective_scheduler.ps1 -Primary
   ```

3. Manual retry test:
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/q20_prospective_scheduler.ps1 -Retry
   ```

4. Confirm task registration:
   ```powershell
   schtasks /Query /TN "SignalForge-Q20-Prospective-Collection" /FO LIST
   ```

5. Confirm task last run result:
   ```powershell
   schtasks /Query /TN "SignalForge-Q20-Prospective-Collection" /FO LIST | Select-String "Last Result|Next Run Time|Status"
   ```

6. Inspect logs:
   ```powershell
   Get-Content "$env:TEMP\signalforge_prospective_collect.log" -Tail 20
   Get-Content "$env:TEMP\signalforge_prospective_collect_errors.log" -Tail 20 -ErrorAction SilentlyContinue
   ```

7. Remove the task:
   ```powershell
   schtasks /Delete /TN "SignalForge-Q20-Prospective-Collection" /F
   ```

---

## Authorization Warning

**DO NOT register this task without explicit authorization.** This script only prepares execution logic; it performs no registration itself. An operator must run the `schtasks /Create` command above after receiving explicit approval. Unauthorized scheduled collection may produce duplicate or overlapping runs contrary to project operational policy.
