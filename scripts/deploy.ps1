
# SignalForge Q15 deployment automation.
# Deploys a frozen release to Development | Staging | Production.
# Fail-closed: any step failure exits 1. All output logged with timestamps.

[CmdletBinding()]
param(
    [ValidateSet("Development","Staging","Production")]
    [string]$Environment = "Staging",

    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Write-Host "ROOT_DEBUG: type=" + $root.GetType().FullName + " value=" + $root

Set-Location $root | Out-Null

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir "deploy-$Environment.log"

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
        $saveEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $output = & $cmdName @cmdArgs 2>&1
        } finally {
            $ErrorActionPreference = $saveEAP
        }
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

# Resolve config path (default: config/<environment>.toml).
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $root "config\$($Environment.ToLower()).toml"
}
Write-Log "Environment: $Environment"
Write-Log "Config path: $ConfigPath"
if (!(Test-Path -LiteralPath $ConfigPath)) {
    Fail "config file not found: $ConfigPath"
}

# Step 1: Verify uv is installed.
Invoke-Step "Verify uv is installed" @("uv","--version")

# Step 2: Verify Python 3.12+.
$pythonVersionLines = Invoke-Step "Verify Python version" @("python","--version")
$pythonVersion = $pythonVersionLines -join " "
if ($pythonVersion -notmatch "Python\s+(\d+)\.(\d+)") {
    Fail "could not parse Python version: $pythonVersion"
}
if ($matches[1] -ne "3" -or [int]$matches[2] -lt 12) {
    Fail "Python 3.12+ required, found $pythonVersion"
}
Write-Log "Python OK: $pythonVersion"

# Step 3: Verify release manifest exists.
$manifestPath = Join-Path $root "docs\validation\results\release_candidate_manifest.json"
if (!(Test-Path -LiteralPath $manifestPath)) {
    Fail "release manifest not found: $manifestPath"
}
Write-Log "Release manifest present: $manifestPath"

# Step 4: Verify config file parses (tomllib).
$parseCheck = '
import sys, tomllib
path = sys.argv[1]
with open(path, "rb") as handle:
    tomllib.load(handle)
print(f"config parses OK: {path}")
'
$parseCheckPath = Join-Path $env:TEMP "sf_parse_check.py"
Set-Content -LiteralPath $parseCheckPath -Value $parseCheck -Encoding UTF8
Invoke-Step "Verify config parses with tomllib" @("python",$parseCheckPath,$ConfigPath)

# Step 5: uv sync (with --extra api when the config declares an [api] section).
$apiCheck = '
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    cfg = tomllib.load(handle)
print("1" if "api" in cfg else "0")
'
$apiCheckPath = Join-Path $env:TEMP "sf_api_probe.py"
Set-Content -LiteralPath $apiCheckPath -Value $apiCheck -Encoding UTF8
$apiProbe = & python $apiCheckPath $ConfigPath 2>&1
if ($LASTEXITCODE -ne 0) { Fail "failed to probe config for [api] section" }
$apiNeeded = (($apiProbe -join "") -replace "\r|\n","")
if ($apiNeeded -eq "1") {
    Invoke-Step "uv sync --extra api" @("uv","sync","--extra","api")
} else {
    Invoke-Step "uv sync" @("uv","sync")
}

# Step 6: Initialize data directories.
$dirs = @(
    "$root\data",
    "$root\logs",
    "$root\backups",
    "$root\models\artifacts"
)
New-Item -ItemType Directory -Path $dirs -Force | Out-Null
Write-Log "Initialized directories: $($dirs -join ", ")"

# Step 7: sf health â€” verify no UNHEALTHY components.
$healthOut = Invoke-Step "sf health" @("uv","run","sf","health")
if ($healthOut -match "UNHEALTHY") {
    Fail "UNHEALTHY component detected in sf health output"
}

# Step 8: sf config-show â€” verify config loads.
Invoke-Step "sf config-show" @("uv","run","sf","config-show","--config",$ConfigPath)

# Step 9: Create backup.
Invoke-Step "sf backup create" @("uv","run","sf","backup","create","--config",$ConfigPath)

# Step 10: Write deployment marker file with timestamp and environment.
$marker = @{
    environment = $Environment
    deployed_utc = (Get-Date -Format "o")
    status = "deployed"
    config_path = $ConfigPath
} | ConvertTo-Json -Depth 5
Set-Content -LiteralPath ("$root\data\deployment-marker.json") -Value $marker -Encoding UTF8
Write-Log "Deployment marker written: data/deployment-marker.json"

Write-Log "DEPLOYMENT SUCCEEDED: $Environment"
exit 0


