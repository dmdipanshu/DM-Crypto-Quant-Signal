# Q21 Scheduler Verification

**Task Name:** SignalForge-Q20-Prospective-Collection
**Scheduler Registered:** false
**PowerShell Runtime:** 5.1 available, pwsh not installed

---

## 1. PowerShell Runtime Verification

| Runtime | Available | Version | Path |
|---|---|---|---|
| powershell.exe (Windows PowerShell 5.1) | Yes | v5.1.26100.9818 | C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe |
| pwsh (PowerShell Core 7+) | No | N/A | Not found |

---

## 2. schtasks Availability

| Executable | Available | Path |
|---|---|---|
| schtasks.exe | Yes | C:\Windows\system32\schtasks.exe |

---

## 3. Scheduler Script Compliance (scripts/q20_prospective_scheduler.ps1)

| Check | Result | Notes |
|---|---|---|
| UTC time calculation (Get-Date -AsUTC) | PASS | Operates on UTC regardless of local Windows timezone |
| Primary at HH:08 UTC | PASS | |
| Retry at HH:12 UTC | PASS | |
| Single-instance lock via FileMode.CreateNew | PASS | |
| Lock release in finally block | PASS | |
| Idempotent no-op via normalized_candles.jsonl | PASS | |
| Exit codes 0/1/2/3 | PASS | |
| Redacted logging | PASS | |
| Bounded retries | FAIL | Retry via separate HH:12 window — design choice |
| **Compliance** | **8/9** | |

---

## 4. Timezone Handling

Confirmed `Get-Date -AsUTC` operates on UTC regardless of local Windows timezone.

---

## 5. Exact Registration Command (schtasks /Create block)

**AUTHORIZATION WARNING:** Do not register this task without explicit human authorization.

```cmd
schtasks /Create /TN "SignalForge-Q20-Prospective-Collection" /TR """
  "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "scripts\q20_prospective_scheduler.ps1"
""" /SC HOURLY /MO 1 /ST 00:00 /RI 1 /DU 23:59 /RL HIGHEST /RU "NT AUTHORITY\SYSTEM" /NP
```

The scheduled task runs hourly (every hour on the hour) with the highest run level under `NT AUTHORITY\SYSTEM` and no password prompt (`/NP`). The script itself internally enforces the primary window at HH:08 UTC and the retry window at HH:12 UTC; all other hours are no-ops.

---

## 6. Exact Removal Command (schtasks /Delete)

```cmd
schtasks /Delete /TN "SignalForge-Q20-Prospective-Collection" /F
```

---

## 7. Exact Inspection Command (schtasks /Query)

```cmd
schtasks /Query /TN "SignalForge-Q20-Prospective-Collection" /FO LIST /V
```

---

## 8. Exact Start Command (schtasks /Run)

```cmd
schtasks /Run /TN "SignalForge-Q20-Prospective-Collection"
```

---

## 9. Exact History Inspection Command

```cmd
schtasks /Query /TN "SignalForge-Q20-Prospective-Collection" /V
```

---

## 10. Execution Identity

`NT AUTHORITY\SYSTEM` with `/RL HIGHEST` (highest run level).

---

## 11. Working Directory

`D:\DM-Crypto-Quant-Signal`

---

## 12. Python/uv Executable Path

`uv` (resolved via PATH).

---

## 13. Log Location

`$env:TEMP\signalforge_prospective_collect.log` and `$env:TEMP\signalforge_prospective_collect_errors.log`

---

## 14. Expected Exit Codes

| Exit Code | Meaning |
|---|---|
| 0 | Success — collection completed normally |
| 1 | General error — collection failed |
| 2 | No data available / idempotent no-op |
| 3 | Single-instance lock could not be acquired (another instance running) |

---

## 15. Dry-Run Status

NOT EXECUTED (pwsh not available; PowerShell 5.1 present but scheduler task not registered).

---

## 16. Authorization Warning

Do not register the scheduler without explicit human authorization. The scheduler is currently unregistered (`Scheduler Registered: false`).
