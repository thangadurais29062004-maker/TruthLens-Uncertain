$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$truthPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path -LiteralPath $truthPython)) { $truthPython = (Get-Command python -ErrorAction Stop).Source }
& $truthPython -c "import sys; assert (3,12) <= sys.version_info[:2] <= (3,13), 'Use Python 3.12 or 3.13'"
if ($LASTEXITCODE -ne 0) { throw 'A supported Python installation is required.' }
& $truthPython -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path 'models/detector.onnx')) {
    & '.venv/Scripts/python.exe' setup_models.py
    if ($LASTEXITCODE -ne 0) { throw 'Model setup failed.' }
}
Write-Host 'Setup complete. Run ./start.ps1 and open http://127.0.0.1:8000.'
