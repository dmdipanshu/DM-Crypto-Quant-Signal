# Q22 Windows PowerShell 5.1 Verification

Date: 2026-11-16

Project: D:\DM-Crypto-Quant-Signal

## PowerShell Runtime Results

| Runtime | Available | Version | Full Path |
|---|---|---|---|
| powershell.exe (Windows PowerShell 5.1) | Yes | 5.1.26100.9818 | C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe |
| pwsh (PowerShell Core 7+) | No | N/A | Not installed |

## schtasks Availability

| Tool | Available | Path |
|---|---|---|
| schtasks.exe | Yes | C:\Windows\system32\schtasks.exe |

## Get-Date -AsUTC Compatibility Test

- PowerShell 5.1: ERROR — "A parameter cannot be found that matches parameter name 'AsUTC'"
- PowerShell 7.1+: Works (not available on this system)
- Replacement: `[DateTime]::UtcNow` — works in both PS 5.1 and 7+

## Scheduler Script Compatibility (scripts/q20_prospective_scheduler.ps1)

- 8 occurrences of `Get-Date -AsUTC` replaced with `[DateTime]::UtcNow`
- 0 remaining `Get-Date -AsUTC` occurrences
- `pwsh` → `powershell.exe` in shebang and usage comments
- `Get-Date -Format` → `[DateTime]::UtcNow.ToString()` for UTC-consistent log timestamps
- PID-based stale lock detection added
- PowerShell 5.1 parser validation: 0 errors
- Exit codes preserved: 0/1/2/3
- Single-instance protection preserved
- Idempotent no-op preserved
- HH:08 primary / HH:12 retry preserved
- Redacted logging preserved

## Dry-Run Test Results

Command: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/q20_prospective_scheduler.ps1 -DryRun`

Exit code: 0

Output: `[2026-11-16T10:57:10Z] [INFO] Dry-run: not a collection minute (current UTC minute is not 08 or 12). No action would be taken.`

Concurrency/stale-lock paths NOT exercised (UTC minute was 56, not 08/12)

## UTC Conversion Verification

- Script uses `[DateTime]::UtcNow` for all UTC calculations
- No double conversion of UTC timestamps
- Log timestamps use `[DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")`
- All minute comparisons (8/12) operate on UTC

## Daylight-Saving Considerations

- `[DateTime]::UtcNow` is not affected by DST — always returns UTC
- Task Scheduler trigger runs every minute; script evaluates UTC minute internally
- Local timezone does not affect collection window determination

## Concurrency and Recovery

- Single-instance lock via `[System.IO.File]::Open` with `FileMode.CreateNew`, `FileShare.None`
- PID stored in lock file for ownership tracking
- On lock failure: checks if owning PID is still running via `[System.Diagnostics.Process]::GetProcessById()`
- Stale lock (dead PID): deleted and acquisition retried
- Active lock (live PID): exit code 2
- Lock released in `finally` block on normal exit
- Best-effort lock release in `catch` block
- Concurrency test NOT executed (UTC minute not 08/12)
- Stale lock recovery NOT executed (UTC minute not 08/12)

## Exact Registration Command (do NOT execute without human authorization)

```
schtasks /Create /TN "SignalForge-Q20-Prospective-Collection" `
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"D:\DM-Crypto-Quant-Signal\scripts\q20_prospective_scheduler.ps1`"" `
  /SC MINUTE /MO 1 `
  /RU "NT AUTHORITY\SYSTEM" `
  /RL HIGHEST `
  /F
```

## Exact Removal Command

```
schtasks /Delete /TN "SignalForge-Q20-Prospective-Collection" /F
```

## Authorization Warning

The scheduler script does NOT register any Windows Task Scheduler task. Registration must be performed separately by an operator with explicit human authorization. Human continuation approval is currently PENDING.
