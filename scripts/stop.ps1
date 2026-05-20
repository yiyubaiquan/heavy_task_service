$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunDir = Join-Path $Root "run"

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessIdToStop)

    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessIdToStop" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessIdToStop ([int]$child.ProcessId)
    }

    $process = Get-Process -Id $ProcessIdToStop -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $ProcessIdToStop -Force -ErrorAction SilentlyContinue
    }
}

foreach ($name in @("worker", "api")) {
    $pidFile = Join-Path $RunDir "$name.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Host "$name is not running: pid file not found"
        continue
    }

    $processIdText = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($processIdText -match '^\d+$') {
        $processId = [int]$processIdText
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
            Stop-ProcessTree -ProcessIdToStop $processId
            Write-Host "Stopped $name, pid=$processId"
        } else {
            Write-Host "$name was not running, stale pid=$processId"
        }
    } else {
        Write-Host "$name has invalid pid file"
    }

    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}
