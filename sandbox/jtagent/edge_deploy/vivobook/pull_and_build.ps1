# VivoBook (Windows boot): pull the newest jtagent GGUF from the TAN Drive
# folder and (re)build the local Ollama model.
#
# Prereqs: rclone remote 'jtagent_tan' (rclone config reconnect jtagent_tan),
# Ollama for Windows installed. Run in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\pull_and_build.ps1
$ErrorActionPreference = "Stop"

$Remote   = if ($env:JTAGENT_RCLONE_REMOTE) { $env:JTAGENT_RCLONE_REMOTE } else { "jtagent_tan" }
$FolderId = if ($env:JTAGENT_TAN_FOLDER_ID) { $env:JTAGENT_TAN_FOLDER_ID } else { "1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB" }
$Dest     = if ($env:JTAGENT_GGUF_DIR) { $env:JTAGENT_GGUF_DIR } else { Join-Path $env:USERPROFILE "jtagent\gguf" }
$Model    = if ($env:JTAGENT_OLLAMA_NAME) { $env:JTAGENT_OLLAMA_NAME } else { "jtagent" }
$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Write-Host "[edge] pulling GGUF from ${Remote}:jtagent/gguf"
rclone copy "${Remote}:jtagent/gguf" $Dest --drive-root-folder-id $FolderId --include "*.gguf" -v

$Gguf = Get-ChildItem -Path $Dest -Filter *.gguf -Recurse |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $Gguf) { throw "no GGUF found under $Dest" }
Write-Host "[edge] newest GGUF: $($Gguf.FullName)"

$Modelfile = Join-Path $Dest "Modelfile"
(Get-Content (Join-Path $Here "Modelfile.template")) `
  -replace '^FROM .*', "FROM $($Gguf.FullName)" | Set-Content $Modelfile

ollama create $Model -f $Modelfile
Write-Host "[edge] built ollama model '$Model'. Try: ollama run $Model"
