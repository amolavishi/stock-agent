$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:HERMES_HOME = "C:\Users\ohjin\AppData\Local\hermes\profiles\stockagent"
Set-Location -LiteralPath $projectRoot
& "C:\Users\ohjin\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe" gateway run
