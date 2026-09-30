param([switch]$BuildWeb)
$ErrorActionPreference = 'Stop'
$serverRoot = Split-Path $PSScriptRoot -Parent
$mobileRoot = Join-Path (Split-Path $serverRoot -Parent) 'p2j-mobile'
$flutter = Join-Path $env:LOCALAPPDATA 'p2j-tools\flutter\bin\flutter.bat'
$python = Join-Path $serverRoot '.venv\Scripts\python.exe'
if ($BuildWeb) {
    Push-Location $mobileRoot
    try {
        & $flutter build web --release --dart-define=USE_MOCK=false --dart-define=API_BASE_URL=/v1
        if ($LASTEXITCODE -ne 0) { throw 'Flutter web build failed.' }
    } finally { Pop-Location }
}
Set-Location $serverRoot
& $python -m uvicorn tools.local_web:app --host 127.0.0.1 --port 8080
