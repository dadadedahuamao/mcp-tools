<#+
.SYNOPSIS
使用项目虚拟环境测试并构建 Windows x64 单文件 db MCP。
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "未找到项目虚拟环境：$python"
}

Push-Location $projectRoot
try {
    & $python -m pytest -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $python -m PyInstaller --noconfirm --clean --onefile --name db-mcp `
        --collect-submodules sqlalchemy.dialects `
        --collect-submodules pymysql `
        --collect-submodules psycopg `
        --collect-submodules oracledb `
        --paths $projectRoot main.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
