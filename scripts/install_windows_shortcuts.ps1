$ErrorActionPreference = "Stop"

$botProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$botStartScript = Join-Path $PSScriptRoot "start_bot.ps1"
$botStopScript = Join-Path $PSScriptRoot "stop_bot.ps1"
$botDesktop = [Environment]::GetFolderPath("Desktop")
$botStartup = [Environment]::GetFolderPath("Startup")
$botPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$botShell = New-Object -ComObject WScript.Shell

$botIcon = $botPowerShell
if ($env:LOCALAPPDATA) {
    $botCodexBin = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin"
    if (Test-Path -LiteralPath $botCodexBin -PathType Container) {
        $botCodexExecutable = Get-ChildItem -LiteralPath $botCodexBin -Filter codex.exe -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -ne $botCodexExecutable) {
            $botIcon = $botCodexExecutable.FullName
        }
    }
}

function New-BotShortcut {
    param(
        [string]$ShortcutPath,
        [string]$ScriptPath,
        [string]$Description
    )

    $botShortcut = $botShell.CreateShortcut($ShortcutPath)
    $botShortcut.TargetPath = $botPowerShell
    $botShortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""
    $botShortcut.WorkingDirectory = $botProjectRoot
    $botShortcut.Description = $Description
    $botShortcut.IconLocation = "$botIcon,0"
    $botShortcut.WindowStyle = 7
    $botShortcut.Save()
}

New-BotShortcut -ShortcutPath (Join-Path $botDesktop "Codex Telegram Bot.lnk") -ScriptPath $botStartScript -Description "Start Codex Telegram Bot"
New-BotShortcut -ShortcutPath (Join-Path $botDesktop "Stop Codex Telegram Bot.lnk") -ScriptPath $botStopScript -Description "Stop Codex Telegram Bot"
New-BotShortcut -ShortcutPath (Join-Path $botStartup "Codex Telegram Bot.lnk") -ScriptPath $botStartScript -Description "Start Codex Telegram Bot when signing in to Windows"
