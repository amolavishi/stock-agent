$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot "data\runtime"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$pidPath = Join-Path $runtimeDir "stock-agent.pid"
if (Test-Path $pidPath) {
    $existingPid = [int](Get-Content $pidPath -Raw)
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$existingPid" -ErrorAction SilentlyContinue
    if ($existing -and $existing.CommandLine -like "*run_stock_agent.ps1*") {
        Write-Output "stock-agent already running pid=$existingPid"
        exit 0
    }
}
$process = Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "run_stock_agent.ps1")) `
    -WorkingDirectory $projectRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runtimeDir "stdout.log") `
    -RedirectStandardError (Join-Path $runtimeDir "stderr.log") -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
Write-Output "stock-agent started pid=$($process.Id)"
