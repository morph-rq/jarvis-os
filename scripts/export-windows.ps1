# Export Jarvis's data from Windows for the move to Omarchy: settings, memory, skills and documents
# Jarvis made, in the layout `jarvis restore` (and install-jarvis.sh --restore) reads.
#
#   powershell -ExecutionPolicy Bypass -File scripts\export-windows.ps1            # to Desktop\jarvis-windows-export-<date>.zip
#   powershell -ExecutionPolicy Bypass -File scripts\export-windows.ps1 -Out D:\jarvis-export.zip
#
# It only READS from %APPDATA%\jarvis-workos and %LOCALAPPDATA%\jarvis-workos and writes one new zip file;
# nothing on Windows is changed or deleted. Keys are NOT exported (re-enter them on Omarchy with
# `jarvis secret set NAME`; the list of the ones you had is in the zip). Virtual environments, downloaded
# models and voices are left out: the Omarchy installer brings fresh ones.
param([string]$Out = "")
$ErrorActionPreference = "Stop"

$config = Join-Path $env:APPDATA "jarvis-workos"
$data = Join-Path $env:LOCALAPPDATA "jarvis-workos"
if (-not (Test-Path $config) -and -not (Test-Path $data)) {
    Write-Error "No Jarvis data found in $config or $data."
}
if (-not $Out) {
    $Out = Join-Path ([Environment]::GetFolderPath("Desktop")) ("jarvis-windows-export-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".zip")
}
$work = Join-Path ([IO.Path]::GetTempPath()) ("jarvis-export-" + [guid]::NewGuid())
$backup = Join-Path $work "backup"
New-Item -ItemType Directory -Force -Path (Join-Path $backup "config"), (Join-Path $backup "data") | Out-Null

$skip = @("venv", "venvs", "models", "voices", "quickshell", "caelestia", "genoffice", "browser", "__pycache__", "run")
if (Test-Path $config) {
    Get-ChildItem -Force $config | Where-Object { $_.Name -ne "secrets.env" } |
        ForEach-Object { Copy-Item -Recurse -Force $_.FullName (Join-Path $backup "config") }
}
if (Test-Path $data) {
    Get-ChildItem -Force $data | Where-Object { $skip -notcontains $_.Name } |
        ForEach-Object { Copy-Item -Recurse -Force $_.FullName (Join-Path $backup "data") }
}

# Which keys were set (names only), so you know what to enter again on Omarchy.
$names = @("GEMINI_API_KEY","ANTHROPIC_API_KEY","HOME_ASSISTANT_TOKEN","MAIL_PASSWORD","TELEGRAM_BOT_TOKEN",
           "SPOTIFY_CLIENT_ID","SPOTIFY_CLIENT_SECRET","CALENDAR_ICS_URLS","COMPOSIO_API_KEY","GOOGLE_OAUTH_CLIENT_ID",
           "GOOGLE_OAUTH_CLIENT_SECRET","DISCORD_BOT_TOKEN","SLACK_BOT_TOKEN","SLACK_APP_TOKEN",
           "WHATSAPP_ACCESS_TOKEN","WHATSAPP_APP_SECRET","WHATSAPP_VERIFY_TOKEN")
$had = @()
$secretsFile = Join-Path $config "secrets.env"
foreach ($n in $names) {
    $inEnv = [Environment]::GetEnvironmentVariable($n, "User") -or [Environment]::GetEnvironmentVariable($n, "Process")
    $inFile = (Test-Path $secretsFile) -and (Select-String -Quiet -Path $secretsFile -Pattern ("^" + $n + "=.+"))
    if ($inEnv -or $inFile) { $had += $n }
}
$had | Set-Content -Encoding utf8 (Join-Path $backup "keys-to-re-enter.txt")

$manifest = @{ created = (Get-Date -Format o); host = $env:COMPUTERNAME; source = "windows"; with_secrets = $false } |
    ConvertTo-Json -Compress
# UTF-8 without a BOM, so the Linux side reads it as plain JSON.
[IO.File]::WriteAllText((Join-Path $backup "manifest.json"), $manifest, (New-Object Text.UTF8Encoding $false))

if (Test-Path $Out) { Remove-Item -Force $Out }
Compress-Archive -Path $backup -DestinationPath $Out
Remove-Item -Recurse -Force $work
Write-Host "Exported to $Out"
Write-Host "Keys to set again on Omarchy (jarvis secret set NAME): $($had -join ', ')"
Write-Host "Copy the zip next to the Jarvis folder on the USB drive, then on Omarchy:  jarvis restore <zip>"
