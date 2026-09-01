Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptsDirectory = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $scriptsDirectory "start-local.ps1"

if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "缺少 Windows 一键启动脚本：$scriptPath"
}

$script = Get-Content -LiteralPath $scriptPath -Raw -Encoding UTF8

function Assert-Contains {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Text.Contains($Expected)) {
        throw $Message
    }
}

function Assert-NotContains {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Unexpected,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if ($Text.Contains($Unexpected)) {
        throw $Message
    }
}

Assert-Contains -Text $script -Expected "CHANGE_ME" -Message "启动脚本未拒绝占位配置"
Assert-Contains -Text $script -Expected "compose config --quiet" -Message "启动脚本未在启动前校验 Compose 配置"
Assert-Contains -Text $script -Expected "compose up --build" -Message "启动脚本未启动完整 Compose 开发环境"
Assert-NotContains -Text $script -Unexpected "Resolve-PythonLauncher" -Message "启动脚本仍依赖宿主机 Python"
Assert-NotContains -Text $script -Unexpected "pnpm" -Message "启动脚本仍依赖宿主机 pnpm"
Assert-NotContains -Text $script -Unexpected "Find-AvailablePort" -Message "启动脚本仍会自动漂移端口"
Assert-NotContains -Text $script -Unexpected "down -v" -Message "启动脚本包含破坏数据库卷的命令"
Assert-NotContains -Text $script -Unexpected "docker volume rm" -Message "启动脚本包含删除 Docker 卷的命令"

Write-Host "Windows Docker 一键启动脚本契约测试通过。" -ForegroundColor Green
