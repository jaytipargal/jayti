# Write the rclone remote 'jtagent_tan' (rooted at the TAN Drive folder) using a
# Google Cloud service-account key — headless, no browser OAuth (VivoBook/Windows).
# The service account's email must be shared on the TAN folder (reader suffices).
#
#   powershell -ExecutionPolicy Bypass -File .\configure_rclone_sa.ps1 C:\path\sa-key.json
param(
  [string]$Key = $env:RCLONE_DRIVE_SERVICE_ACCOUNT_FILE
)
$ErrorActionPreference = "Stop"

$Remote   = if ($env:JTAGENT_RCLONE_REMOTE) { $env:JTAGENT_RCLONE_REMOTE } else { "jtagent_tan" }
$FolderId = if ($env:JTAGENT_TAN_FOLDER_ID) { $env:JTAGENT_TAN_FOLDER_ID } else { "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB" }

if (-not $Key -or -not (Test-Path $Key)) {
  throw "usage: configure_rclone_sa.ps1 C:\path\sa-key.json (a Google Cloud service-account JSON key)"
}
if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) { throw "rclone not installed (winget install Rclone.Rclone)" }

$KeyAbs = (Resolve-Path $Key).Path

# Non-interactive: (re)create the remote; any old token/client fields are dropped.
rclone config delete $Remote 2>$null | Out-Null
rclone config create $Remote drive scope=drive root_folder_id=$FolderId service_account_file=$KeyAbs --non-interactive | Out-Null

Write-Host "[rclone] remote '$Remote' -> folder $FolderId via service account $KeyAbs"
Write-Host "[rclone] verifying (lists the TAN folder):"
rclone lsd "${Remote}:" --max-depth 1
