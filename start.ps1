$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    Write-Host 'Create the environment first: py -3.12 -m venv .venv'
    Write-Host 'Then install: .venv/Scripts/python.exe -m pip install -r requirements.txt'
    exit 1
}
& '.venv/Scripts/python.exe' server.py
