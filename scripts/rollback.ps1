
# SignalForge Q15 rollback procedure.
# Restores the most recent backup (or -BackupId) from backups/.
# -DryRun defaults to $true: preview only, nothing written.

[CmdletBinding()]
param(
    [string]$BackupId = "",
    [switch]$DryRun = $true
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root | Out-Null

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir "rollback.log"

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ"
    $line = "[$timestamp] $Message"
    Write-Host $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Fail {
    param([string]$Message)
    Write-Log "FATAL: $Message"
    exit 1
}

function Invoke-Step {
    param([string]$Description, [string[]]$Command)
    Write-Log "STEP: $Description"
    Write-Log "CMD: $($Command -join " ")"
    try {
        $cmdName = $Command[0]
        $cmdArgs = @($Command[1..($Command.Length - 1)])
        $output = & $cmdName @cmdArgs 2>&1
    } catch {
        Write-Log "FAILED (exception): $Description : $_"
        exit 1
    }
    if ($null -ne $output) {
        $output | ForEach-Object { Write-Log $_ }
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "FAILED (exit $LASTEXITCODE): $Description"
        exit 1
    }
    return $output
}

Write-Log "Rollback started: DryRun=$DryRun BackupId=$BackupId"

# Step 1: List available backups via the sf CLI.
Invoke-Step "List available backups in backups/" @("uv","run","sf","backup","list","backups/")

# Resolve backup id: use -BackupId when provided, otherwise the most recent.
$backupRoot = Join-Path $root "backups"
if (!(Test-Path -LiteralPath $backupRoot)) {
    Fail "backups/ directory not found"
}

if (-not [string]::IsNullOrWhiteSpace($BackupId)) {
    $resolvedId = $BackupId.Trim()
    Write-Log "Using specified backup id: $resolvedId"
} else {
    Write-Log "No -BackupId provided; selecting most recent backup."
    $candidates = @()
    Get-ChildItem -LiteralPath $backupRoot -Directory | ForEach-Object {
        $m = Join-Path $_.FullName "manifest.json"
        if (Test-Path -LiteralPath $m) {
            try {
                $candidates += Get-Content -LiteralPath $m -Raw | ConvertFrom-Json
            } catch {
                Write-Log "WARNING: unreadable manifest $m"
            }
        }
    }
    if ($candidates.Count -eq 0) {
        Fail "no backups found in backups/"
    }
    $latest = ($candidates | Sort-Object created_utc -Descending)[0]
    $resolvedId = $latest.backup_id
    Write-Log "Most recent backup: $resolvedId (created_utc=$($latest.created_utc))"
}

$backupDir = Join-Path $backupRoot $resolvedId
if (!(Test-Path -LiteralPath $backupDir)) {
    Fail "backup directory not found: $backupDir"
}
$manifestPath = Join-Path $backupDir "manifest.json"
if (!(Test-Path -LiteralPath $manifestPath)) {
    Fail "manifest.json missing in $backupDir"
}

# Step 2 / 3: Dry run reports what would be restored without writing.
if ($DryRun) {
    Write-Log "DRY RUN mode: no files will be written or modified"
    Invoke-Step "Preview restore of backup $resolvedId" @("uv","run","sf","backup","restore",$backupDir,"--dry-run")
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    Write-Log "Would restore $($manifest.files.Count) files: $($manifest.files -join ", ")"
    Write-Log "DRY RUN complete: nothing written"
    exit 0
}

# Not dry run: preview first, then execute the restore.
Invoke-Step "Dry-run preview of restore $resolvedId" @("uv","run","sf","backup","restore",$backupDir,"--dry-run")
Invoke-Step "Restore backup $resolvedId" @("uv","run","sf","backup","restore",$backupDir,"--no-dry-run")

# Step 4: Verify restored files match manifest checksums.
$verifyCheck = '
import json, hashlib, sys
from pathlib import Path
backup_id = sys.argv[1]
backup_root = Path("backups") / backup_id
manifest_path = backup_root / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
default_dirs = ["models/artifacts", "data", "data/paper", "logs"]
ok = True
for rel in manifest["files"]:
    expected = manifest["checksums"][rel]
    checked = False
    for src in (list(manifest.get("source_paths") or []) + default_dirs):
        target = Path(src) / rel
        if target.is_file():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != expected:
                print("MISMATCH " + rel + ": expected " + expected + " got " + actual)
                ok = False
            checked = True
            break
    if not checked:
        print("NOT_FOUND " + rel)
        ok = False
if ok:
    print("checksums OK: " + str(len(manifest["files"])) + " files verified against manifest")
    sys.exit(0)
else:
    print("checksum verification FAILED")
    sys.exit(1)
'
$verifyOut = & python $verifyCheck $resolvedId 2>&1
if ($null -ne $verifyOut) {
    $verifyOut | ForEach-Object { Write-Log $_ }
}
if ($LASTEXITCODE -ne 0) {
    Fail "restored files do not match manifest checksums"
}

# Step 5: sf health after restore.
$healthOut = Invoke-Step "sf health after restore" @("uv","run","sf","health")
if ($healthOut -match "UNHEALTHY") {
    Fail "UNHEALTHY component detected in sf health output after restore"
}

Write-Log "ROLLBACK SUCCEEDED: backup $resolvedId restored (DryRun=$DryRun)"
exit 0
