param([switch]$ValidateOnly)

$ErrorActionPreference = "Stop"

$codexStartApp = Get-StartApps |
    Where-Object { $_.AppID -like "OpenAI.Codex_*!App" } |
    Select-Object -First 1

if ($null -eq $codexStartApp) {
    throw "Codex Desktop was not found in the Start menu."
}

$codexDesktopProcesses = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.ExecutablePath -like "*\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe" -and
            $_.CommandLine -notmatch "--type="
        }
)

if ($ValidateOnly) {
    [ordered]@{
        appId = $codexStartApp.AppID
        processIds = @($codexDesktopProcesses | Select-Object -ExpandProperty ProcessId)
    } | ConvertTo-Json -Compress
    exit 0
}

foreach ($codexDesktopProcess in $codexDesktopProcesses) {
    Stop-Process -Id $codexDesktopProcess.ProcessId -Force -ErrorAction Stop
}

$codexStopDeadline = [DateTime]::UtcNow.AddSeconds(10)
while ([DateTime]::UtcNow -lt $codexStopDeadline) {
    $codexStillRunning = @(
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.ExecutablePath -like "*\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe" -and
                $_.CommandLine -notmatch "--type="
            }
    )
    if ($codexStillRunning.Count -eq 0) {
        break
    }
    Start-Sleep -Milliseconds 250
}

$codexExplorer = Join-Path $env:SystemRoot "explorer.exe"
Start-Process -FilePath $codexExplorer -ArgumentList "shell:AppsFolder\$($codexStartApp.AppID)"

if ($codexDesktopProcesses.Count -gt 0) {
    "RESTARTED"
}
else {
    "STARTED"
}
