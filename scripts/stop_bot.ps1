$ErrorActionPreference = "Stop"

$botProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$botPidFile = Join-Path $botProjectRoot "data\bot.pid.json"

if (-not (Test-Path -LiteralPath $botPidFile)) {
    exit 0
}

try {
    $botState = Get-Content -Raw -LiteralPath $botPidFile | ConvertFrom-Json
    $botProcess = Get-Process -Id ([int]$botState.pid) -ErrorAction Stop
    $expectedStart = [DateTime]::Parse([string]$botState.startedAt).ToUniversalTime()
    $actualStart = $botProcess.StartTime.ToUniversalTime()
    $isExpectedProcess = $botProcess.ProcessName -match '^pythonw?$' -and
        [Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -lt 2
    if ($isExpectedProcess) {
        Stop-Process -Id $botProcess.Id -ErrorAction Stop
    }
}
catch {
    # A missing process means that the PID file was stale.
}
finally {
    Remove-Item -LiteralPath $botPidFile -Force -ErrorAction SilentlyContinue
}
