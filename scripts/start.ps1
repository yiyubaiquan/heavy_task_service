param(
    [switch]$SkipSync
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunDir = Join-Path $Root "run"
$LogDir = Join-Path $Root "logs"
$EnvFile = Join-Path $Root ".env"
$EnvExample = Join-Path $Root ".env.example"

New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null
Set-Location $Root

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Default
    )

    if (-not (Test-Path $EnvFile)) {
        return $Default
    }

    $line = Get-Content $EnvFile | Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } | Select-Object -First 1
    if (-not $line) {
        return $Default
    }

    $value = ($line -split "=", 2)[1].Trim()
    $value = $value.Trim('"').Trim("'")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

function Import-DotEnv {
    if (-not (Test-Path $EnvFile)) {
        return
    }

    foreach ($line in Get-Content $EnvFile) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -notmatch "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$") {
            continue
        }

        $name = $Matches[1]
        $value = $Matches[2].Trim().Trim('"').Trim("'")
        Set-Item -Path "Env:$name" -Value $value
    }
}

function Test-PidFileRunning {
    param([Parameter(Mandatory = $true)][string]$PidFile)

    if (-not (Test-Path $PidFile)) {
        return $false
    }

    $processIdText = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not ($processIdText -match '^\d+$')) {
        return $false
    }

    return [bool](Get-Process -Id ([int]$processIdText) -ErrorAction SilentlyContinue)
}

function Start-ManagedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $pidFile = Join-Path $RunDir "$Name.pid"
    if (Test-PidFileRunning $pidFile) {
        $existingPid = Get-Content $pidFile | Select-Object -First 1
        Write-Host "$Name is already running, pid=$existingPid"
        return
    }

    $stdoutLog = Join-Path $LogDir "$Name.out.log"
    $stderrLog = Join-Path $LogDir "$Name.err.log"
    $process = Start-Process -FilePath "uv" `
        -ArgumentList $Arguments `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru

    Set-Content -Path $pidFile -Value $process.Id -Encoding ASCII
    Write-Host "Started $Name, pid=$($process.Id), logs=$stdoutLog / $stderrLog"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv was not found in PATH. Install uv first: https://docs.astral.sh/uv/"
}

if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Copy-Item $EnvExample $EnvFile
    Write-Host "Created .env from .env.example. Please verify REDIS_URL before production use."
}

Import-DotEnv

if (-not $SkipSync) {
    uv sync --locked
}

$apiHost = Get-DotEnvValue -Name "API_HOST" -Default "0.0.0.0"
$apiPort = Get-DotEnvValue -Name "API_PORT" -Default "8010"
$queue = Get-DotEnvValue -Name "CELERY_TASK_DEFAULT_QUEUE" -Default "heavy_tasks"
$workerConcurrency = Get-DotEnvValue -Name "CELERY_WORKER_CONCURRENCY" -Default "1"

Start-ManagedProcess -Name "api" -Arguments @("run", "uvicorn", "app.main:app", "--host", $apiHost, "--port", $apiPort)
Start-ManagedProcess -Name "worker" -Arguments @("run", "celery", "-A", "app.workers.celery_app.celery_app", "worker", "-Q", $queue, "--loglevel=INFO", "--pool=solo", "--concurrency=$workerConcurrency")

Write-Host "Heavy Task Service started. API: http://127.0.0.1:$apiPort"
