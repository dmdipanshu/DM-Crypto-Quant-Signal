#!/usr/bin/env powershell.exe
# Q20 Prospective Collection Scheduler â€” Dry-Run Preparation Only
#
# This script implements the execution logic for scheduling Q5.2 prospective
# collection runs. It does NOT register a Windows Task Scheduler task.
# Registration must be performed separately by an operator with explicit
# authorization.
#
# Usage (dry-run):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/q20_prospective_scheduler.ps1 -DryRun
#
# Usage (primary, manual test):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/q20_prospective_scheduler.ps1 -Primary
#
# Usage (retry, manual test):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/q20_prospective_scheduler.ps1 -Retry
#
# Exit codes:
#   0 - Success / no-op (wrong minute)
#   1 - Collection error
#   2 - Lock held by another process (no overlapping runs)
#   3 - Script error

[CmdletBinding()]
param(
    [switch] $DryRun,
    [switch] $Primary,
    [switch] $Retry,
    [string] $LogPath = "$env:TEMP\signalforge_prospective_collect.log",
    [string] $LockPath = "$env:TEMP\signalforge_prospective_collect.lock",
    [string] $SimulatedUtcTime
)

# ---------------------------------------------------------------------------
# Test-only UTC clock injection
# ---------------------------------------------------------------------------
if ($PSBoundParameters.ContainsKey('SimulatedUtcTime')) {
    $parsed = [DateTime]::Parse($SimulatedUtcTime, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
    if ($parsed.Kind -ne [DateTimeKind]::Utc) {
        Write-Error "SimulatedUtcTime must represent a UTC time (e.g. 2026-11-17T08:00:00Z)"
        exit 3
    }
    $script:SimulatedUtcNow = $parsed
}

$ErrorActionPreference = 'Stop'
$scriptRoot = (Get-Item -Path ".\").FullName
$collectionScript = Join-Path $scriptRoot "scripts\run_q5_prospective_collection.py"
$uvExe = "uv"

# ---------------------------------------------------------------------------
# Logging (redacted â€” no credentials or secrets)
# ---------------------------------------------------------------------------
function Write-Log {
    param([string] $Message, [string] $Level = "INFO")
    $timestamp = (Get-TestUtcNow).ToString("yyyy-MM-ddTHH:mm:ssZ")
    $line = "[$timestamp] [$Level] $Message"
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
    Write-Host $line
}

function Write-ErrorLog {
    param([string] $Message)
    $errorLog = $LogPath -replace "\.log$", "_errors.log"
    $timestamp = (Get-TestUtcNow).ToString("yyyy-MM-ddTHH:mm:ssZ")
    $line = "[$timestamp] [ERROR] $Message"
    Add-Content -Path $errorLog -Value $line -Encoding UTF8
    Write-Host $line
}

# ---------------------------------------------------------------------------
# Lock acquisition (single-instance protection)
# ---------------------------------------------------------------------------
function Acquire-Lock {
    param([string] $LockPath)

    # Attempt to acquire the lock exclusively
    try {
        $stream = [System.IO.File]::Open(
            $LockPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $stream.Close()
        # Write owning PID so stale locks can be detected
        Set-Content -Path $LockPath -Value $PID -Encoding UTF8 -ErrorAction Stop
        return $true
    }
    catch {
        # Lock creation failed — either held by a live process or stale
        $ownerPid = $null
        try {
            $content = Get-Content -Path $LockPath -Encoding UTF8 -ErrorAction Stop
            $pidText = ($content | Select-Object -First 1).Trim()
            if (-not [string]::IsNullOrWhiteSpace($pidText)) {
                $ownerPid = [int]::Parse($pidText)
            }
        }
        catch {
            # Lock file empty, unreadable, or missing → treat as stale
        }

        if ($null -ne $ownerPid) {
            try {
                $proc = [System.Diagnostics.Process]::GetProcessById($ownerPid)
                if ($null -ne $proc) {
                    # Owning process is still running — lock is valid
                    Write-Log "Another collection process (PID $ownerPid) is holding the lock; exiting to prevent overlap." "WARN"
                    return $false
                }
            }
            catch {
                # Process does not exist → stale lock
            }
        }

        # Stale lock: remove and retry acquisition once
        try {
            if (Test-Path -Path $LockPath) {
                Remove-Item -Path $LockPath -Force -ErrorAction Stop
            }
        }
        catch {
            Write-Log "Could not remove stale lock file at $LockPath; exiting." "WARN"
            return $false
        }

        try {
            $stream = [System.IO.File]::Open(
                $LockPath,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
            $stream.Close()
            Set-Content -Path $LockPath -Value $PID -Encoding UTF8 -ErrorAction Stop
            return $true
        }
        catch {
            Write-Log "Failed to acquire lock after stale-lock cleanup; exiting." "WARN"
            return $false
        }
    }
}

function Release-Lock {
    param([string] $LockPath)
    try {
        if (Test-Path -Path $LockPath) {
            Remove-Item -Path $LockPath -Force -ErrorAction SilentlyContinue
        }
    }
    catch {
        # Best-effort cleanup; do not fail on lock release errors
    }
}

# ---------------------------------------------------------------------------
# Test-only UTC clock source
# ---------------------------------------------------------------------------
function Get-TestUtcNow {
    if ($script:SimulatedUtcNow -ne $null) {
        return $script:SimulatedUtcNow
    }
    return [DateTime]::UtcNow
}

# ---------------------------------------------------------------------------
# Determine if we should run now based on UTC minute
# ---------------------------------------------------------------------------
function Get-CollectionWindow {
    $nowUtc = Get-TestUtcNow
    $minute = $nowUtc.Minute

    if ($minute -eq 8) {
        return "primary"
    }
    if ($minute -eq 12) {
        return "retry"
    }
    return "none"
}

# ---------------------------------------------------------------------------
# Check if current hour's candle already collected (idempotent no-op)
# ---------------------------------------------------------------------------
function Test-CandleAlreadyCollected {
    param([string] $ScriptRoot)
    $storagePath = Join-Path $ScriptRoot "data\prospective\binance\spot\BTC\USDT\1h\normalized_candles.jsonl"
    if (-not (Test-Path -Path $storagePath)) {
        return $false
    }

    $nowUtc2 = Get-TestUtcNow
    $currentHourUtc = $nowUtc2.AddMinutes(-($nowUtc2.Minute % 60)).AddSeconds(-$nowUtc2.Second).AddMilliseconds(-$nowUtc2.Millisecond)
    $currentHourIso = $currentHourUtc.ToString("o")

    try {
        $content = Get-Content -Path $storagePath -Encoding UTF8 -ErrorAction SilentlyContinue
        foreach ($line in $content) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $record = $line | ConvertFrom-Json -ErrorAction SilentlyContinue
            if ($record -and $record.open_time -eq $currentHourIso) {
                return $true
            }
        }
    }
    catch {
        # If we can't read storage, don't skip â€” let the collector decide
        return $false
    }
    return $false
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
try {
    $window = Get-CollectionWindow

    if ($window -eq "none") {
        # Not a scheduled collection minute â€” no-op
        if ($DryRun) {
            Write-Log "Dry-run: not a collection minute (current UTC minute is not 08 or 12). No action would be taken."
        }
        exit 0
    }

    # Determine which mode to run
    $runMode = $window
    if ($Primary -and $window -eq "retry") {
        Write-Log "Primary flag set but current window is retry; skipping."
        exit 0
    }
    if ($Retry -and $window -eq "primary") {
        Write-Log "Retry flag set but current window is primary; skipping."
        exit 0
    }
    if ($Primary) { $runMode = "primary" }
    if ($Retry) { $runMode = "retry" }

    if ($DryRun) {
        Write-Log "Dry-run: would run $runMode collection at $((Get-TestUtcNow).ToString("yyyy-MM-ddTHH:mm:ssZ")) UTC"
        Write-Log "Dry-run: command would be: uv run python scripts/run_q5_prospective_collection.py --once"
        Write-Log "Dry-run: lock path: $LockPath"
        Write-Log "Dry-run: log path: $LogPath"
        Write-Log "Dry-run: single-instance protection enabled"
        Write-Log "Dry-run: idempotent no-op check enabled (skip if current hour candle already collected)"
        exit 0
    }

    # Acquire lock (single-instance protection)
    $locked = Acquire-Lock -LockPath $LockPath
    if (-not $locked) {
        exit 2
    }

    try {
        # Idempotent no-op: check if current hour's candle already collected
        $alreadyCollected = Test-CandleAlreadyCollected -ScriptRoot $scriptRoot
        if ($alreadyCollected) {
            Write-Log "Current hour's candle already collected; skipping collection (idempotent no-op)."
            exit 0
        }

        # Run collection
        Write-Log "Starting $runMode collection run..."
        $process = Start-Process -FilePath $uvExe -ArgumentList "run", "python", $collectionScript, "--once" -WorkingDirectory $scriptRoot -Wait -NoNewWindow -PassThru
        $exitCode = $process.ExitCode

        if ($exitCode -eq 0) {
            Write-Log "$runMode collection completed successfully (exit code 0)."
            exit 0
        }
        else {
            Write-Log "$runMode collection failed with exit code $exitCode." "ERROR"
            Write-ErrorLog "$runMode collection failed with exit code $exitCode at $((Get-TestUtcNow).ToString("yyyy-MM-ddTHH:mm:ssZ")) UTC"
            exit 1
        }
    }
    finally {
        Release-Lock -LockPath $LockPath
    }
}
catch {
    Write-Log "Script error: $($_.Exception.Message)" "ERROR"
    Write-ErrorLog "Script error: $($_.Exception.Message) at $((Get-TestUtcNow).ToString("yyyy-MM-ddTHH:mm:ssZ")) UTC"
    # Best-effort lock release
    try { Release-Lock -LockPath $LockPath } catch {}
    exit 3
}








