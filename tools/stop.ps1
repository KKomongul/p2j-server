$ErrorActionPreference = 'Stop'
$serverRoot = Split-Path $PSScriptRoot -Parent
foreach ($name in @('web','desktop')) {
    $pidFile = Join-Path $serverRoot "logs\$name.pid"
    if (-not (Test-Path $pidFile)) { continue }
    $serverId = [int](Get-Content $pidFile)
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $serverId"
    if (-not $proc) { continue }
    if ($proc.ExecutablePath -ne (Join-Path $serverRoot '.venv\Scripts\python.exe') -or
        $proc.CommandLine -notmatch '(app\.main:app|tools\.local_web:app)') {
        throw 'Process identity changed; refusing to stop.'
    }
    taskkill /PID $serverId /T /F
    if ($LASTEXITCODE -ne 0) { throw 'Stop failed.' }
}
