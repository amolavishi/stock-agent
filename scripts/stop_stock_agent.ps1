$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $projectRoot "data\runtime\stock-agent.pid"
if (-not (Test-Path $pidPath)) { Write-Output "stock-agent is not running"; exit 0 }
$targetPid = [int](Get-Content $pidPath -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid" -ErrorAction SilentlyContinue
if ($process -and $process.CommandLine -like "*run_stock_agent.ps1*") {
    Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $targetPid } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $pidPath -Force
Write-Output "stock-agent stopped"
