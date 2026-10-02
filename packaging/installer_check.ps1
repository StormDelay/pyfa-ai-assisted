<# Install a built pyfa-mcp setup silently into a throwaway user profile, and
   check it registers, serves MCP and uninstalls cleanly.

     powershell -File packaging\installer_check.ps1 -Setup dist\pyfa-mcp-v0.1.0-pyfa2.69.0-setup.exe

   Only the Codex task is on, and USERPROFILE / HOME / APPDATA / LOCALAPPDATA
   point into the throwaway profile: the installer's client checks and
   pyfa-mcp.exe --register read those, so neither can reach the real user's
   configs. #>
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"

$T = Join-Path ([IO.Path]::GetTempPath()) ("pyfa-mcp-installer-" + [guid]::NewGuid())
$H = Join-Path $T "home"
New-Item -ItemType Directory "$H\.codex", "$H\AppData\Roaming", "$H\AppData\Local" -Force | Out-Null
$env:USERPROFILE = $H
$env:HOME = $H
$env:APPDATA = "$H\AppData\Roaming"
$env:LOCALAPPDATA = "$H\AppData\Local"
$codex = "$H\.codex\config.toml"

$tasks = "codex,!claude_desktop,!claude_code,!cursor,!windsurf,!devin,!vscode"
$p = Start-Process (Resolve-Path $Setup) -Wait -PassThru -ArgumentList `
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$T\app", "/MERGETASKS=$tasks", "/LOG=$T\install.log"
if ($p.ExitCode -ne 0) { Write-Host "install failed: exit $($p.ExitCode)"; exit 1 }
if (Select-String -Path "$T\install.log" -Pattern "Registration failures" -Quiet) {
    Get-Content "$T\install.log" | Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
    Write-Host "registration failed"; exit 1
}
if (-not ((Test-Path $codex) -and (Select-String -Path $codex -Pattern "pyfa-mcp.exe" -SimpleMatch -Quiet))) {
    Write-Host "the Codex task did not register pyfa-mcp"; exit 1
}

& $Python packaging\mcp_smoke.py "$T\app\pyfa-mcp.exe" --data-dir "$T\data" --pyfa-dir "$T\no-pyfa"
if ($LASTEXITCODE -ne 0) { Write-Host "the installed exe failed the smoke"; exit 1 }

Start-Process "$T\app\unins000.exe" -Wait -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" | Out-Null
# Inno's uninstaller relaunches itself from a temp copy: wait for the files, not the process.
$deadline = (Get-Date).AddSeconds(120)
while ((Test-Path "$T\app\pyfa-mcp.exe") -and ((Get-Date) -lt $deadline)) { Start-Sleep -Seconds 2 }
if (Test-Path "$T\app\pyfa-mcp.exe") { Write-Host "not uninstalled"; exit 1 }
if (Select-String -Path $codex -Pattern "mcp_servers.pyfa" -SimpleMatch -Quiet) {
    Write-Host "uninstall left the Codex entry behind"; exit 1
}
Write-Host "installer ok"
# Said out loud: a native command's last exit code would otherwise become ours.
exit 0
