Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptsDirectory = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $scriptsDirectory "start-local.ps1"

if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "缺少 Windows 一键启动脚本：$scriptPath"
}

. $scriptPath

function Assert-True {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Condition,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)]
        $Expected,

        [Parameter(Mandatory = $true)]
        $Actual,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($Expected -ne $Actual) {
        throw "$Message。期望：$Expected；实际：$Actual"
    }
}

$paths = Get-ProjectPaths -ScriptDirectory $scriptsDirectory
$expectedRoot = Split-Path -Parent $scriptsDirectory
Assert-Equal -Expected $expectedRoot -Actual $paths.ProjectRoot -Message "项目根目录解析错误"
Assert-Equal -Expected (Join-Path $expectedRoot "backend") -Actual $paths.BackendDirectory -Message "后端目录解析错误"
Assert-Equal -Expected (Join-Path $expectedRoot "frontend") -Actual $paths.FrontendDirectory -Message "前端目录解析错误"
Assert-Equal -Expected "Local" -Actual $DatabaseMode -Message "开发环境应默认使用本地 MySQL"

$exitedProcess = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-NonInteractive", "-Command", "exit 0") `
    -WindowStyle Hidden `
    -PassThru
$exitedProcess.WaitForExit()
$waitForUrlError = ""
try {
    Wait-ForUrl `
        -Name "测试服务" `
        -Url "http://127.0.0.1:1" `
        -Process $exitedProcess `
        -LogDirectory (Join-Path $expectedRoot "logs")
}
catch {
    $waitForUrlError = $_.Exception.Message
}
Assert-True `
    -Condition ($waitForUrlError -like "测试服务启动进程已退出*") `
    -Message "服务名称与中文相邻时未生成正确的进程退出错误"

$temporaryEnvPath = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText(
        $temporaryEnvPath,
        "# 测试配置`r`nMYSQL_HOST=127.0.0.1`r`nMYSQL_PORT='3306'`r`nDATABASE_URL=`"mysql+aiomysql://user:pass@127.0.0.1:3306/app`"`r`n",
        [System.Text.UTF8Encoding]::new($false)
    )

    $envValues = Read-DotEnvFile -Path $temporaryEnvPath
    Assert-Equal -Expected "127.0.0.1" -Actual $envValues["MYSQL_HOST"] -Message "MYSQL_HOST 解析错误"
    Assert-Equal -Expected "3306" -Actual $envValues["MYSQL_PORT"] -Message "单引号环境变量解析错误"
    Assert-Equal -Expected "mysql+aiomysql://user:pass@127.0.0.1:3306/app" -Actual $envValues["DATABASE_URL"] -Message "双引号环境变量解析错误"
}
finally {
    [System.IO.File]::Delete($temporaryEnvPath)
}

$incompleteLocalDatabaseConfig = @{
    MYSQL_HOST  = "127.0.0.1"
    MYSQL_PORT  = "3306"
    DATABASE_URL = "mysql+aiomysql://user:pass@127.0.0.1:3306/app"
}

$missingLocalConfigRejected = $false
try {
    Assert-DatabaseConfiguration -Values $incompleteLocalDatabaseConfig -Mode "Local"
}
catch {
    $missingLocalConfigRejected = $_.Exception.Message -like "*MYSQL_DATABASE*"
}
Assert-True -Condition $missingLocalConfigRejected -Message "本地模式未校验完整数据库配置"

$validLocalDatabaseConfig = @{
    MYSQL_HOST  = "127.0.0.1"
    MYSQL_PORT  = "3306"
    MYSQL_DATABASE = "app"
    MYSQL_USER = "user"
    MYSQL_PASSWORD = "pass"
    DATABASE_URL = "mysql+aiomysql://user:pass@127.0.0.1:3306/app"
}
Assert-DatabaseConfiguration -Values $validLocalDatabaseConfig -Mode "Local"

$placeholderRejected = $false
try {
    Assert-DatabaseConfiguration -Values @{
        MYSQL_HOST  = "127.0.0.1"
        MYSQL_PORT  = "3306"
        MYSQL_DATABASE = "app"
        MYSQL_USER = "user"
        MYSQL_PASSWORD = "pass"
        DATABASE_URL = "mysql+aiomysql://CHANGE_ME:CHANGE_ME@127.0.0.1:3306/app"
    } -Mode "Local"
}
catch {
    $placeholderRejected = $_.Exception.Message -like "*DATABASE_URL*"
}
Assert-True -Condition $placeholderRejected -Message "本地模式未拒绝 DATABASE_URL 占位值"

$missingDockerConfigRejected = $false
try {
    Assert-DatabaseConfiguration -Values $validLocalDatabaseConfig -Mode "Docker"
}
catch {
    $missingDockerConfigRejected = $_.Exception.Message -like "*MYSQL_ROOT_PASSWORD*"
}
Assert-True -Condition $missingDockerConfigRejected -Message "Docker 模式未校验容器数据库配置"

$validDockerDatabaseConfig = $validLocalDatabaseConfig.Clone()
$validDockerDatabaseConfig["MYSQL_ROOT_PASSWORD"] = "root-pass"
Assert-DatabaseConfiguration -Values $validDockerDatabaseConfig -Mode "Docker"

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()

try {
    $busyPort = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    Assert-True -Condition (Test-TcpPortInUse -Port $busyPort) -Message "未识别正在监听的端口"
    Assert-True -Condition (Test-TcpEndpoint -HostName "127.0.0.1" -Port $busyPort -TimeoutMilliseconds 1000) -Message "未识别可访问的本地 MySQL 端点"

    $availablePort = Find-AvailablePort -StartPort $busyPort
    Assert-True -Condition ($availablePort -ne $busyPort) -Message "未跳过正在监听的端口"
}
finally {
    $listener.Stop()
}

$temporaryListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$temporaryListener.Start()
$freePort = ([System.Net.IPEndPoint]$temporaryListener.LocalEndpoint).Port
$temporaryListener.Stop()

Assert-Equal -Expected $freePort -Actual (Find-AvailablePort -StartPort $freePort) -Message "空闲端口不应被改写"
Assert-True -Condition ((Find-AvailablePort -StartPort $freePort -ExcludedPorts @($freePort)) -ne $freePort) -Message "未跳过明确排除的端口"

Write-Host "Windows 一键启动脚本测试通过。" -ForegroundColor Green
