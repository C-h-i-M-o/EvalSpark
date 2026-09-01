[CmdletBinding()]
param(
    [ValidateSet("Local", "Docker")]
    [string]$DatabaseMode = "Local",
    [string]$BackendHost = $(if ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { "127.0.0.1" }),
    [string]$FrontendHost = $(if ($env:FRONTEND_HOST) { $env:FRONTEND_HOST } elseif ($env:REACT_FRONTEND_HOST) { $env:REACT_FRONTEND_HOST } else { "127.0.0.1" }),
    [ValidateRange(1, 65535)]
    [int]$BackendPort = $(if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8000 }),
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = $(if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } elseif ($env:REACT_FRONTEND_PORT) { [int]$env:REACT_FRONTEND_PORT } else { 5174 })
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Log {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host "[MultiChatEval React] $Message" -ForegroundColor Blue
}

function Write-WarningLog {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host "[MultiChatEval React] $Message" -ForegroundColor Yellow
}

function Get-ProjectPaths {
    param([Parameter(Mandatory = $true)][string]$ScriptDirectory)

    $projectRoot = Split-Path -Parent $ScriptDirectory
    [PSCustomObject]@{
        ProjectRoot      = $projectRoot
        BackendDirectory = Join-Path $projectRoot "backend"
        FrontendDirectory = Join-Path $projectRoot "frontend"
        LogDirectory     = Join-Path $projectRoot "logs"
    }
}

function Read-DotEnvFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "未找到环境变量文件：$Path"
    }

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmedLine = $line.Trim()
        if (-not $trimmedLine -or $trimmedLine.StartsWith("#")) {
            continue
        }

        if ($trimmedLine -notmatch "^(?<key>[A-Za-z_][A-Za-z0-9_]*)=(?<value>.*)$") {
            continue
        }

        $key = $Matches["key"]
        $value = $Matches["value"].Trim()
        if ($value.Length -ge 2) {
            $firstCharacter = $value.Substring(0, 1)
            $lastCharacter = $value.Substring($value.Length - 1, 1)
            if (($firstCharacter -eq '"' -and $lastCharacter -eq '"') -or ($firstCharacter -eq "'" -and $lastCharacter -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        $values[$key] = $value
    }

    return $values
}

function Assert-DatabaseConfiguration {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Values,
        [Parameter(Mandatory = $true)][ValidateSet("Local", "Docker")][string]$Mode
    )

    $requiredKeys = @("MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD", "DATABASE_URL")
    if ($Mode -eq "Docker") {
        $requiredKeys += "MYSQL_ROOT_PASSWORD"
    }

    foreach ($key in $requiredKeys) {
        if (-not $Values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace([string]$Values[$key])) {
            throw ".env 中缺少数据库配置：$key"
        }

        if ([string]$Values[$key] -like "*CHANGE_ME*") {
            throw ".env 中的 $key 仍是 CHANGE_ME，请填写真实的本地数据库连接信息。"
        }
    }

    $mysqlPort = 0
    if (-not [int]::TryParse([string]$Values["MYSQL_PORT"], [ref]$mysqlPort) -or $mysqlPort -lt 1 -or $mysqlPort -gt 65535) {
        throw ".env 中的 MYSQL_PORT 必须是 1 到 65535 之间的整数。"
    }
}

function Test-TcpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][ValidateRange(1, 65535)][int]$Port,
        [ValidateRange(1, 60000)][int]$TimeoutMilliseconds = 1000
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connectTask = $client.ConnectAsync($HostName, $Port)
        if (-not $connectTask.Wait($TimeoutMilliseconds)) {
            return $false
        }

        $null = $connectTask.GetAwaiter().GetResult()
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Wait-ForLocalMySql {
    param([Parameter(Mandatory = $true)][hashtable]$Values)

    $mysqlHost = [string]$Values["MYSQL_HOST"]
    $mysqlPort = [int]$Values["MYSQL_PORT"]
    Write-Log "等待本地 MySQL 就绪：${mysqlHost}:$mysqlPort"

    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if (Test-TcpEndpoint -HostName $mysqlHost -Port $mysqlPort) {
            Write-Log "本地 MySQL 端口已就绪。"
            return
        }

        Start-Sleep -Seconds 2
    }

    throw "本地 MySQL 在 60 秒内未就绪，请确认 Windows MySQL 服务已启动，并检查 .env 中的 MYSQL_HOST 和 MYSQL_PORT。"
}

function Test-TcpPortInUse {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 65535)]
        [int]$Port
    )

    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return $null -ne ($listeners | Where-Object { $_.Port -eq $Port } | Select-Object -First 1)
}

function Find-AvailablePort {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 65535)]
        [int]$StartPort,

        [int[]]$ExcludedPorts = @()
    )

    $port = $StartPort
    while ($port -le 65535) {
        if (($ExcludedPorts -notcontains $port) -and -not (Test-TcpPortInUse -Port $port)) {
            return $port
        }

        Write-WarningLog "端口 $port 已被占用或保留，尝试端口 $($port + 1)。"
        $port++
    }

    throw "从端口 $StartPort 开始未找到可用端口。"
}

function Resolve-RequiredCommand {
    param(
        [Parameter(Mandatory = $true)][string[]]$Candidates,
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][string]$InstallHint
    )

    foreach ($candidate in $Candidates) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $command) {
            return $command.Source
        }
    }

    throw "未找到 $DisplayName。$InstallHint"
}

function Resolve-PythonLauncher {
    $candidates = @()

    if ($env:PYTHON_BIN) {
        $candidates += [PSCustomObject]@{ FilePath = $env:PYTHON_BIN; PrefixArguments = @() }
    }

    $pyCommand = Get-Command "py.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $pyCommand) {
        $candidates += [PSCustomObject]@{ FilePath = $pyCommand.Source; PrefixArguments = @("-3") }
    }

    $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $pythonCommand) {
        $candidates += [PSCustomObject]@{ FilePath = $pythonCommand.Source; PrefixArguments = @() }
    }

    foreach ($candidate in $candidates) {
        try {
            & $candidate.FilePath @($candidate.PrefixArguments) -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
        catch {
            continue
        }
    }

    throw "未找到 Python 3.11 或更高版本。请安装 Python，或通过 PYTHON_BIN 指定解释器路径。"
}

function Assert-LastExitCode {
    param([Parameter(Mandatory = $true)][string]$FailureMessage)

    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Wait-ForMySql {
    param([Parameter(Mandatory = $true)][string]$DockerCommand)

    Write-Log "等待 MySQL 就绪..."
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        & $DockerCommand exec multichateval-mysql mysqladmin ping -h localhost *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Log "MySQL 已就绪。"
            return
        }

        Start-Sleep -Seconds 2
    }

    throw "MySQL 在 60 秒内未就绪，请检查 Docker 或数据库日志。"
}

function Wait-ForUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$LogDirectory
    )

    Write-Log "等待${Name}就绪..."
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "${Name}启动进程已退出，请检查 $LogDirectory 下的日志。"
        }

        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
            Write-Log "$Name 已就绪：$Url"
            return
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }

    throw "$Name 在 30 秒内未就绪，请检查 $LogDirectory 下的日志。"
}

function Start-LoggedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StandardOutputPath,
        [Parameter(Mandatory = $true)][string]$StandardErrorPath
    )

    return Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -NoNewWindow `
        -PassThru `
        -RedirectStandardOutput $StandardOutputPath `
        -RedirectStandardError $StandardErrorPath
}

function Stop-ManagedProcessTree {
    param(
        [AllowNull()]
        [System.Diagnostics.Process]$Process,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if ($null -eq $Process) {
        return
    }

    $Process.Refresh()
    if ($Process.HasExited) {
        return
    }

    Write-Log "停止$Name..."
    & "$env:SystemRoot\System32\taskkill.exe" /PID $Process.Id /T /F *> $null
}

function Get-HealthCheckHost {
    param([Parameter(Mandatory = $true)][string]$ListenHost)

    if ($ListenHost -in @("0.0.0.0", "::", "[::]")) {
        return "127.0.0.1"
    }

    return $ListenHost
}

function Invoke-StartLocal {
    $paths = Get-ProjectPaths -ScriptDirectory $PSScriptRoot
    $backendProcess = $null
    $frontendProcess = $null

    $pnpmCommand = Resolve-RequiredCommand -Candidates @("pnpm.cmd", "pnpm.exe", "pnpm") -DisplayName "pnpm" -InstallHint "请先安装 pnpm。"
    $pythonLauncher = Resolve-PythonLauncher

    if (-not (Test-Path -LiteralPath $paths.LogDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $paths.LogDirectory | Out-Null
    }

    $envPath = Join-Path $paths.ProjectRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $paths.ProjectRoot ".env.example") -Destination $envPath
        Write-Log "未发现 .env，已从 .env.example 复制一份。"
    }

    $databaseValues = Read-DotEnvFile -Path $envPath
    Assert-DatabaseConfiguration -Values $databaseValues -Mode $DatabaseMode

    $dockerCommand = $null
    if ($DatabaseMode -eq "Docker") {
        $dockerCommand = Resolve-RequiredCommand -Candidates @("docker.exe", "docker") -DisplayName "Docker" -InstallHint "请先安装并启动 Docker Desktop。"
        & $dockerCommand compose version *> $null
        Assert-LastExitCode "当前 Docker 未提供 Compose 插件，请安装或升级 Docker Desktop。"
    }

    try {
        if ($DatabaseMode -eq "Docker") {
            Write-Log "启动 Docker MySQL 容器..."
            Push-Location $paths.ProjectRoot
            try {
                & $dockerCommand compose up -d mysql
                Assert-LastExitCode "MySQL 容器启动失败，请检查 Docker Desktop。"
            }
            finally {
                Pop-Location
            }

            Wait-ForMySql -DockerCommand $dockerCommand
        }
        else {
            Wait-ForLocalMySql -Values $databaseValues
        }

        $venvPython = Join-Path $paths.BackendDirectory ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            Write-Log "创建后端虚拟环境..."
            & $pythonLauncher.FilePath @($pythonLauncher.PrefixArguments) -m venv (Join-Path $paths.BackendDirectory ".venv")
            Assert-LastExitCode "后端虚拟环境创建失败。"
        }

        Write-Log "校验并安装后端依赖..."
        Push-Location $paths.BackendDirectory
        try {
            & $venvPython -m pip install -e ".[dev]"
            Assert-LastExitCode "后端依赖安装失败。"
        }
        finally {
            Pop-Location
        }

        Write-Log "按锁文件校验并安装 React 前端依赖..."
        Push-Location $paths.FrontendDirectory
        try {
            & $pnpmCommand install --frozen-lockfile
            Assert-LastExitCode "React 前端依赖安装失败。"
        }
        finally {
            Pop-Location
        }

        $actualBackendPort = Find-AvailablePort -StartPort $BackendPort
        $actualFrontendPort = Find-AvailablePort -StartPort $FrontendPort -ExcludedPorts @($actualBackendPort)

        Write-Log "执行数据库迁移..."
        Push-Location $paths.BackendDirectory
        try {
            & $venvPython -m alembic upgrade head
            Assert-LastExitCode "数据库迁移失败。"
        }
        finally {
            Pop-Location
        }

        $backendOutputLog = Join-Path $paths.LogDirectory "backend-react.log"
        $backendErrorLog = Join-Path $paths.LogDirectory "backend-react-error.log"
        Write-Log "启动后端：http://${BackendHost}:$actualBackendPort"
        $backendProcess = Start-LoggedProcess `
            -FilePath $venvPython `
            -Arguments @("-m", "uvicorn", "app.main:app", "--reload", "--host", $BackendHost, "--port", "$actualBackendPort") `
            -WorkingDirectory $paths.BackendDirectory `
            -StandardOutputPath $backendOutputLog `
            -StandardErrorPath $backendErrorLog

        $backendCheckHost = Get-HealthCheckHost -ListenHost $BackendHost
        Wait-ForUrl `
            -Name "后端服务" `
            -Url "http://${backendCheckHost}:$actualBackendPort/api/health" `
            -Process $backendProcess `
            -LogDirectory $paths.LogDirectory

        $previousBackendTarget = [Environment]::GetEnvironmentVariable("VITE_BACKEND_TARGET", "Process")
        $previousDevPort = [Environment]::GetEnvironmentVariable("VITE_DEV_PORT", "Process")
        try {
            [Environment]::SetEnvironmentVariable("VITE_BACKEND_TARGET", "http://${backendCheckHost}:$actualBackendPort", "Process")
            [Environment]::SetEnvironmentVariable("VITE_DEV_PORT", "$actualFrontendPort", "Process")

            $frontendOutputLog = Join-Path $paths.LogDirectory "frontend.log"
            $frontendErrorLog = Join-Path $paths.LogDirectory "frontend-error.log"
            Write-Log "启动 React 前端：http://${FrontendHost}:$actualFrontendPort"
            $frontendProcess = Start-LoggedProcess `
                -FilePath $pnpmCommand `
                -Arguments @("dev", "--host", $FrontendHost, "--port", "$actualFrontendPort", "--strictPort") `
                -WorkingDirectory $paths.FrontendDirectory `
                -StandardOutputPath $frontendOutputLog `
                -StandardErrorPath $frontendErrorLog
        }
        finally {
            [Environment]::SetEnvironmentVariable("VITE_BACKEND_TARGET", $previousBackendTarget, "Process")
            [Environment]::SetEnvironmentVariable("VITE_DEV_PORT", $previousDevPort, "Process")
        }

        $frontendCheckHost = Get-HealthCheckHost -ListenHost $FrontendHost
        Wait-ForUrl `
            -Name "React 前端服务" `
            -Url "http://${frontendCheckHost}:$actualFrontendPort" `
            -Process $frontendProcess `
            -LogDirectory $paths.LogDirectory

        Write-Log "React 版本全栈项目已启动。日志目录：$($paths.LogDirectory)"
        Write-Log "按 Ctrl+C 停止后端和 React 前端开发服务。"

        while ($true) {
            Start-Sleep -Seconds 1
            $backendProcess.Refresh()
            $frontendProcess.Refresh()

            if ($backendProcess.HasExited) {
                throw "后端服务已退出，请查看 $backendOutputLog 和 $backendErrorLog。"
            }

            if ($frontendProcess.HasExited) {
                throw "React 前端服务已退出，请查看 $frontendOutputLog 和 $frontendErrorLog。"
            }
        }
    }
    finally {
        Stop-ManagedProcessTree -Process $frontendProcess -Name "React 前端服务"
        Stop-ManagedProcessTree -Process $backendProcess -Name "后端服务"
    }
}

if ($MyInvocation.InvocationName -ne ".") {
    try {
        Invoke-StartLocal
    }
    catch [System.Management.Automation.PipelineStoppedException] {
        Write-WarningLog "收到停止信号。"
    }
    catch {
        Write-Host "[MultiChatEval React] $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}
