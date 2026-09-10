[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envPath = Join-Path $projectRoot ".env"
$envExamplePath = Join-Path $projectRoot ".env.example"

function Write-Info([string]$Message) {
    Write-Host "[agent] $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[ok] $Message" -ForegroundColor Green
}

function Write-WarningMessage([string]$Message) {
    Write-Host "[warning] $Message" -ForegroundColor Yellow
}

function Test-PortInUse([int]$CandidatePort) {
    $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return [bool]($listeners | Where-Object { $_.Port -eq $CandidatePort })
}

function Get-FreePort([int]$RequestedPort) {
    $candidate = $RequestedPort
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Test-PortInUse $candidate)) {
            return $candidate
        }
        $candidate++
    }
    throw "Could not find an available port between $RequestedPort and $($RequestedPort + 19)."
}

function Import-ProxySettings([string]$ConfigPath) {
    foreach ($proxyKey in @("HTTP_PROXY", "HTTPS_PROXY")) {
        $line = Get-Content -LiteralPath $ConfigPath | Where-Object {
            $_ -match ("^\s*" + $proxyKey + "\s*=")
        } | Select-Object -First 1
        if ($line) {
            $value = ($line -split "=", 2)[1].Trim()
            if ($value -and -not $value.StartsWith("#")) {
                $value = $value.Trim('"').Trim("'")
                [Environment]::SetEnvironmentVariable($proxyKey, $value, "Process")
            }
        }
    }
}

Set-Location $projectRoot
Write-Info "Project root: $projectRoot"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error "Missing project Python: $pythonPath. Create the virtual environment and install requirements first."
    exit 1
}

if (-not (Test-Path -LiteralPath $envPath)) {
    if (-not (Test-Path -LiteralPath $envExamplePath)) {
        Write-Error "Neither .env nor .env.example exists in $projectRoot."
        exit 1
    }
    Copy-Item -LiteralPath $envExamplePath -Destination $envPath
    Write-Info "Created .env from .env.example. Add proxy or AI settings there when needed."
} else {
    Write-Ok ".env already exists; it was not overwritten."
}

Import-ProxySettings $envPath
$proxyConfigured = $false
foreach ($proxyKey in @("HTTP_PROXY", "HTTPS_PROXY")) {
    if ([Environment]::GetEnvironmentVariable($proxyKey, "Process")) {
        $proxyConfigured = $true
    }
    if (Select-String -LiteralPath $envPath -Pattern ("^\s*" + $proxyKey + "\s*=\s*(?!\s*(#|$)).+") -Quiet) {
        $proxyConfigured = $true
    }
}
if ($proxyConfigured) {
    Write-Ok "HTTP/HTTPS proxy configuration detected (value hidden)."
} else {
    Write-Info "No HTTP/HTTPS proxy configured; direct HTTPS will be used."
}

$probeCode = @'
import sys

from hotspot_agent.config import load_local_env

load_local_env()

try:
    import httpx
    response = httpx.get(
        "https://hnrss.org/newest?q=AI",
        timeout=8,
        follow_redirects=True,
        trust_env=True,
    )
    print(f"HTTP {response.status_code}")
    sys.exit(0 if response.status_code < 500 else 2)
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}")
    sys.exit(1)
'@

Write-Info "Checking Python HTTPS access (one RSS endpoint)..."
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$probePath = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), ".py")
$previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
if ($previousPythonPath) {
    $env:PYTHONPATH = "$projectRoot;$previousPythonPath"
} else {
    $env:PYTHONPATH = $projectRoot
}
try {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($probePath, $probeCode, $utf8NoBom)
    $probeOutput = @(& $pythonPath $probePath 2>&1)
    $probeExitCode = $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
    if ($previousPythonPath) {
        $env:PYTHONPATH = $previousPythonPath
    } else {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
}
$ErrorActionPreference = $previousErrorActionPreference
$probeText = ($probeOutput -join " ").Trim()
if ($probeExitCode -eq 0) {
    Write-Ok "Python HTTPS probe succeeded: $probeText"
} elseif ($probeText -match "10013") {
    Write-WarningMessage "Python HTTPS probe was blocked with WinError 10013. Allow $pythonPath outbound HTTPS in Windows Firewall/security software. The service will still start, but live scans may fall back to demo data."
} else {
    Write-WarningMessage "Python HTTPS probe did not succeed ($probeText). The service will still start; inspect /runs after a scan for source-level errors."
}

$selectedPort = Get-FreePort $Port
if ($selectedPort -ne $Port) {
    Write-WarningMessage "Port $Port is already in use; using port $selectedPort instead."
}

$baseUrl = "http://127.0.0.1:$selectedPort"
$candidatesUrl = "$baseUrl/candidates"
if ($CheckOnly) {
    Write-Info "Check-only mode complete. The service was not started."
    Write-Info "Candidate URL if started now: $candidatesUrl"
    exit 0
}

Write-Info "Starting uvicorn on $baseUrl"
$serverArguments = @(
    "-m", "uvicorn", "app:app", "--reload",
    "--host", "127.0.0.1", "--port", "$selectedPort"
)
$serverProcess = $null
$cancelHandler = $null
try {
    $serverProcess = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList $serverArguments `
        -WorkingDirectory $projectRoot `
        -NoNewWindow `
        -PassThru

    $cancelHandler = [ConsoleCancelEventHandler]{
        param($sender, $eventArgs)
        $eventArgs.Cancel = $true
        if ($script:serverProcess -and -not $script:serverProcess.HasExited) {
            Stop-Process -Id $script:serverProcess.Id -Force -ErrorAction SilentlyContinue
        }
    }
    [Console]::add_CancelKeyPress($cancelHandler)

    $healthUrl = "$baseUrl/health"
    $ready = $false
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if ($serverProcess.HasExited) {
            throw "uvicorn exited before becoming ready (exit code $($serverProcess.ExitCode))."
        }
        try {
            $healthResponse = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
            if ([int]$healthResponse.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            # The reloader needs a short moment before /health is available.
        }
        Start-Sleep -Milliseconds 500
    }

    if ($ready) {
        Write-Ok "Service is ready: $candidatesUrl"
        if (-not $NoBrowser) {
            Start-Process $candidatesUrl
            Write-Info "Opened the candidate pool in the default browser."
        }
    } else {
        Write-WarningMessage "The service did not answer /health within 15 seconds. Keep this window open to inspect uvicorn logs."
    }

    Write-Info "Press Ctrl+C to stop the service."
    Wait-Process -Id $serverProcess.Id
} finally {
    if ($cancelHandler) {
        [Console]::remove_CancelKeyPress($cancelHandler)
    }
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Write-Info "Stopping uvicorn process $($serverProcess.Id)."
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
