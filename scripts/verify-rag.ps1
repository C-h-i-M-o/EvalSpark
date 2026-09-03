param(
    [ValidateSet('unit', 'integration')]
    [string]$Mode = 'unit'
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$composeArgs = @('compose', '--project-directory', $repoRoot, '--project-name', 'evalspark-rag-test',
    '--env-file', (Join-Path $repoRoot 'docker/rag-test.env'), '-f', (Join-Path $repoRoot 'docker-compose.rag-test.yml'))

function Invoke-TestCompose {
    param([string[]]$Arguments)
    & docker @composeArgs @Arguments
    if ($LASTEXITCODE -ne 0) { throw "隔离验收命令失败，退出码：$LASTEXITCODE。测试数据保留，不自动清理。" }
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw '请先安装 Docker Desktop。' }
    & docker info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0) { throw 'Docker 尚未就绪。请在资源充足设备手动启动后再验收。' }
    Write-Host "隔离验收：项目 evalspark-rag-test；数据库 mysql-test / multichateval_rag_test；模式 $Mode"
    Invoke-TestCompose -Arguments @('--profile', 'unit', '--profile', 'lifecycle', 'config', '--quiet')
    if ($Mode -eq 'unit') {
        Invoke-TestCompose -Arguments @('--profile', 'unit', 'build', 'unit-runner', 'frontend-test')
        Invoke-TestCompose -Arguments @('run', '--rm', '--no-deps', 'unit-runner', 'python', '-m', 'pip', 'check')
        Invoke-TestCompose -Arguments @('run', '--rm', '--no-deps', 'unit-runner')
        Invoke-TestCompose -Arguments @('run', '--rm', '--no-deps', 'frontend-test')
        Invoke-TestCompose -Arguments @('run', '--rm', '--no-deps', 'frontend-test', 'pnpm', 'build')
    } else {
        & docker volume inspect evalspark_rag_model_cache --format '{{.Name}}' 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw '缺少固定模型缓存卷。确认下载授权后运行 docker volume create evalspark_rag_model_cache，再重新验收。禁止删除或替换已有卷。'
        }
        Invoke-TestCompose -Arguments @('build', 'runner')
        Invoke-TestCompose -Arguments @('--profile', 'lifecycle', 'stop', 'worker-test')
        Invoke-TestCompose -Arguments @('up', '-d', '--wait', '--wait-timeout', '180', 'mysql-test')
        # 先迁移测试库；不能在旧 schema 上启动 Worker 恢复循环。
        Invoke-TestCompose -Arguments @('run', '--rm', '--no-deps', 'runner')
        Invoke-TestCompose -Arguments @('--profile', 'lifecycle', 'up', '-d', '--wait', '--wait-timeout', '1800',
            'embedding-test', 'qdrant-test', 'redis-test', 'model-test')
        Invoke-TestCompose -Arguments @('--profile', 'lifecycle', 'up', '-d', 'worker-test')
        Invoke-TestCompose -Arguments @('run', '--rm', '--no-deps', 'acceptance-runner')
    }
    Write-Host '当前档自动检查已结束。浏览器、真实付费模型、业务升级及性能验收仍需单独记录；测试服务与数据保留。'
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
