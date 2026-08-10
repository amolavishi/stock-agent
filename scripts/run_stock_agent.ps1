$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$runtimePython = $env:STOCK_AGENT_PYTHON
if (-not $runtimePython) {
    $runtimePython = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $runtimePython)) {
    $runtimePython = (Get-Command python -ErrorAction Stop).Source
}
& $runtimePython main.py discord
