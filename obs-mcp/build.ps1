<#+
.SYNOPSIS
Tests and builds the Windows x64 single-file OBS MCP executable.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Virtual environment not found: $python"
}

Push-Location $projectRoot
try {
    & $python -m pytest -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m PyInstaller --noconfirm --clean --onefile --name obs-mcp --paths $projectRoot main.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
