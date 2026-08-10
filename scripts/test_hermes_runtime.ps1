$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:HERMES_HOME = "C:\Users\ohjin\AppData\Local\hermes\profiles\stockagent"
$hermes = "C:\Users\ohjin\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe"
Set-Location -LiteralPath $projectRoot
& $hermes config check
& $hermes mcp test stock-agent
& $hermes skills list
