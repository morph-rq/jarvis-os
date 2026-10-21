<#
Drive the Try Omarchy test VM from Windows. The VM is only a test bed; Jarvis targets upstream Omarchy.

  .\scripts\vm.ps1 start     launch Try Omarchy (quick-start account, SSH on 2222, this repo shared)
  .\scripts\vm.ps1 version   print guest vs. pinned upstream Omarchy version
  .\scripts\vm.ps1 deploy    install Jarvis in the guest from the shared folder (keys passed over SSH stdin)
  .\scripts\vm.ps1 shot NAME capture the guest screen (in-guest grim) into evidence\NAME.png
  .\scripts\vm.ps1 ssh CMD   run a command in the guest
#>
param([Parameter(Position = 0)][string]$Action = "start", [Parameter(Position = 1, ValueFromRemainingArguments)][string[]]$Rest)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Exe = Join-Path $Repo "vm\tryomarchy-v0.12.0\TryOmarchy.exe"
$Key = Join-Path $env:USERPROFILE ".ssh\jarvis_tryomarchy"
$SshArgs = @("-i", $Key, "-p", "2222", "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=$env:USERPROFILE\.ssh\known_hosts_tryomarchy", "-o", "ConnectTimeout=5", "omarchy@127.0.0.1")

function Guest([string]$Command) { & ssh @SshArgs $Command }

switch ($Action) {
  "start" {
    # An explicit -dir skips the install-location prompt; this is the launcher's own default.
    Start-Process -FilePath $Exe -ArgumentList @("-dir", "`"$env:LOCALAPPDATA\TryOmarchy`"", "-instant", "-ssh", "2222",
      "-ssh-key", "`"$Key.pub`"", "-share", "`"$Repo`"")
  }
  "version" {
    $upstream = git -C (Join-Path $Repo "upstream\omarchy") describe --tags
    Write-Output "upstream quattro: $upstream ($(git -C (Join-Path $Repo 'upstream\omarchy') rev-parse HEAD))"
    Guest 'echo "guest: $(omarchy-version 2>/dev/null || cat $OMARCHY_PATH/version) | $(pacman -Q omarchy 2>/dev/null) | quickshell $(pacman -Q quickshell 2>/dev/null | cut -d" " -f2) | hyprland $(hyprctl version -j | jq -r .tag)"'
  }
  "deploy" {
    $secrets = Join-Path $env:APPDATA "jarvis\secrets.env"
    # Keys travel over the SSH channel's stdin, never on a command line or in the shared folder.
    Get-Content $secrets | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object { "export $_" } |
      & ssh @SshArgs 'set -a; source <(tr -d "\r"); set +a; cd ~/jarvis && bash omarchy/install.sh'
  }
  "shot" {
    $name = if ($Rest) { $Rest[0] } else { "shot-$(Get-Date -Format HHmmss)" }
    $out = Join-Path $Repo "evidence\$name.png"
    New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null
    Guest "grim /tmp/$name.png"
    & scp -i $Key -P 2222 -o "UserKnownHostsFile=$env:USERPROFILE\.ssh\known_hosts_tryomarchy" "omarchy@127.0.0.1:/tmp/$name.png" $out
    Write-Output $out
  }
  "ssh" { Guest ($Rest -join " ") }
  default { throw "unknown action $Action" }
}
