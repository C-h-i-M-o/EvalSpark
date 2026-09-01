[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Log {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host "[EvalSpark Docker] $Message" -ForegroundColor Blue
}

function Assert-LastExitCode {
    param([Parameter(Mandatory = $true)][string]$FailureMessage)

    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Resolve-DockerCommand {
    $command = Get-Command "docker.exe", "docker" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        throw "未找到 Docker。请先安装并启动 Docker Desktop。"
    }

    return $command.Source
}

function Assert-EnvironmentFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "未找到环境变量文件：$Path。请先复制 .env.example 为 .env 并完成配置。"
    }

    $placeholderLine = Get-Content -LiteralPath $Path -Encoding UTF8 |
        Where-Object { $_ -match "^\s*[A-Za-z_][A-Za-z0-9_]*=.*CHANGE_ME" } |
        Select-Object -First 1

    if ($null -ne $placeholderLine) {
        throw ".env 中仍存在 CHANGE_ME 占位值，请完成配置后再启动。"
    }
}

function Invoke-StartLocal {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $envPath = Join-Path $projectRoot ".env"
    Assert-EnvironmentFile -Path $envPath

    $dockerCommand = Resolve-DockerCommand

    & $dockerCommand info *> $null
    Assert-LastExitCode "Docker daemon 不可用，请确认 Docker Desktop 已启动。"

    & $dockerCommand compose version *> $null
    Assert-LastExitCode "当前 Docker 未提供 Compose 插件，请安装或升级 Docker Desktop。"

    Push-Location $projectRoot
    try {
        Write-Log "校验 Docker Compose 配置..."
        & $dockerCommand compose config --quiet
        Assert-LastExitCode "Docker Compose 配置无效，请检查 .env 和 docker-compose.yml。"

        Write-Log "构建并启动 MySQL、迁移、后端和 React 前端..."
        Write-Log "默认前端：http://127.0.0.1:5174；默认后端：http://127.0.0.1:8000；如 .env 覆盖端口请以 Compose 映射为准。"
        Write-Log "按 Ctrl+C 停止本次开发环境；普通停止不会删除数据库卷。"
        & $dockerCommand compose up --build
        Assert-LastExitCode "Docker Compose 开发环境已异常退出，请检查上方容器日志。"
    }
    finally {
        Pop-Location
    }
}

if ($MyInvocation.InvocationName -ne ".") {
    try {
        Invoke-StartLocal
    }
    catch [System.Management.Automation.PipelineStoppedException] {
        Write-Host "[EvalSpark Docker] 收到停止信号。" -ForegroundColor Yellow
    }
    catch {
        Write-Host "[EvalSpark Docker] $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}
