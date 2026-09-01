param([switch]$ValidateOnly)

$ErrorActionPreference = "Stop"

$botProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$botEntryPoint = Join-Path $botProjectRoot "bot.py"
$botDataDir = Join-Path $botProjectRoot "data"
$botPidFile = Join-Path $botDataDir "bot.pid.json"
$botLogFile = Join-Path $botDataDir "bot.log"
$botOutputFile = Join-Path $botDataDir "bot.stdout.log"
$botLauncherLog = Join-Path $botDataDir "launcher.log"

New-Item -ItemType Directory -Path $botDataDir -Force | Out-Null

function Test-BotProcess {
    param([object]$State)

    if ($null -eq $State -or $null -eq $State.pid -or $null -eq $State.startedAt) {
        return $false
    }
    try {
        $botProcess = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
        if ($botProcess.ProcessName -notmatch '^pythonw?$') {
            return $false
        }
        $expectedStart = [DateTime]::Parse([string]$State.startedAt).ToUniversalTime()
        $actualStart = $botProcess.StartTime.ToUniversalTime()
        return [Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -lt 2
    }
    catch {
        return $false
    }
}

try {
    if (Test-Path -LiteralPath $botPidFile) {
        try {
            $botState = Get-Content -Raw -LiteralPath $botPidFile | ConvertFrom-Json
        }
        catch {
            $botState = $null
        }
        if (Test-BotProcess $botState) {
            exit 0
        }
        Remove-Item -LiteralPath $botPidFile -Force -ErrorAction SilentlyContinue
    }

    $botPythonCandidates = @()
    $botVenvPython = Join-Path $botProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $botVenvPython -PathType Leaf) {
        $botPythonCandidates += $botVenvPython
    }
    if ($env:LOCALAPPDATA) {
        foreach ($botPythonVersion in @("3.14", "3.13", "3.12", "3.11")) {
            $botCompactVersion = $botPythonVersion.Replace(".", "")
            $botPythonCandidates += Join-Path $env:LOCALAPPDATA "Python\pythoncore-$botPythonVersion-64\python.exe"
            $botPythonCandidates += Join-Path $env:LOCALAPPDATA "Python\pythoncore-$botPythonVersion-arm64\python.exe"
            $botPythonCandidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python$botCompactVersion\python.exe"
        }
        $botPythonRoot = Join-Path $env:LOCALAPPDATA "Python"
        if (Test-Path -LiteralPath $botPythonRoot -PathType Container) {
            try {
                $botPythonCandidates += Get-ChildItem -LiteralPath $botPythonRoot -Filter python.exe -File -Recurse -ErrorAction Stop |
                    Sort-Object LastWriteTime -Descending |
                    Select-Object -ExpandProperty FullName
            }
            catch {
                # Direct candidates above still work when directory enumeration is restricted.
            }
        }
    }
    $botPathPython = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $botPathPython) {
        $botPythonCandidates += $botPathPython.Source
    }
    $botPython = $botPythonCandidates |
        Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
        Select-Object -First 1
    if (-not $botPython) {
        throw "Python 3 was not found. Install Python or create .venv in the project."
    }
    if ($ValidateOnly) {
        Write-Output "Project: $botProjectRoot"
        Write-Output "Python: $botPython"
        exit 0
    }

    $botStartParameters = @{
        FilePath = $botPython
        ArgumentList = @("`"$botEntryPoint`"")
        WorkingDirectory = $botProjectRoot
        WindowStyle = "Hidden"
        RedirectStandardOutput = $botOutputFile
        RedirectStandardError = $botLogFile
        PassThru = $true
    }
    $botProcess = Start-Process @botStartParameters

    $botState = [ordered]@{
        pid = $botProcess.Id
        startedAt = $botProcess.StartTime.ToUniversalTime().ToString("o")
        executable = $botPython
    }
    $botState | ConvertTo-Json -Compress | Set-Content -LiteralPath $botPidFile -Encoding UTF8
}
catch {
    $message = "{0:o} Start failed: {1}" -f [DateTime]::UtcNow, $_.Exception.Message
    Add-Content -LiteralPath $botLauncherLog -Value $message -Encoding UTF8
    exit 1
}
