# 每日采集：尽量自动拉起 Docker / WeWe RSS，再跑公众号简报和英文网页简报。
# 用法:
#   .\run_daily.ps1              # 完整跑一次
#   .\run_daily.ps1 -CheckOnly   # 只检查前置条件，不采集
#   .\run_daily.ps1 -SkipWechat
#   .\run_daily.ps1 -SkipWeb

param(
    [switch]$CheckOnly,
    [switch]$SkipWechat,
    [switch]$SkipWeb
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

$logsDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$logFile = Join-Path $logsDir "daily_$stamp.log"
$pythonPathFile = Join-Path $logsDir "python_path.txt"

function Write-Log([string]$Message, [string]$Level = "INFO") {
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

function Resolve-PythonPath {
    if ($env:DAILY_PYTHON -and (Test-Path $env:DAILY_PYTHON)) {
        return $env:DAILY_PYTHON
    }
    if (Test-Path $pythonPathFile) {
        $saved = (Get-Content $pythonPathFile -Raw).Trim()
        if ($saved -and (Test-Path $saved)) { return $saved }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }
    foreach ($candidate in @(
            "D:\software\anaconda\python.exe",
            "$env:USERPROFILE\anaconda3\python.exe",
            "$env:USERPROFILE\miniconda3\python.exe"
        )) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Get-DockerDesktopExe {
    foreach ($candidate in @(
            "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
            "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe"
        )) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Test-DockerEngine {
    docker info 1>$null 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Start-DockerDesktopIfNeeded {
    if (Test-DockerEngine) { return $true }
    $exe = Get-DockerDesktopExe
    if (-not $exe) {
        Write-Log "未找到 Docker Desktop 安装路径" "WARN"
        return $false
    }
    $running = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
    if (-not $running) {
        Write-Log "正在启动 Docker Desktop: $exe"
        Start-Process $exe | Out-Null
    } else {
        Write-Log "Docker Desktop 进程在，但引擎尚未就绪，继续等待"
    }
    $deadline = (Get-Date).AddMinutes(4)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerEngine) { return $true }
        Start-Sleep -Seconds 5
    }
    return (Test-DockerEngine)
}

function Test-WeweRss {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:4000/" -UseBasicParsing -TimeoutSec 8
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Start-WeweRssIfNeeded {
    if (-not (Test-DockerEngine)) { return $false }
    Write-Log "确保 WeWe RSS 容器按 compose 配置运行"
    docker compose -f docker-compose.wechat.yml up -d 2>&1 | ForEach-Object { Write-Log $_ }
    $deadline = (Get-Date).AddMinutes(2)
    while ((Get-Date) -lt $deadline) {
        if (Test-WeweRss) { return $true }
        Start-Sleep -Seconds 4
    }
    return (Test-WeweRss)
}

function Invoke-PythonStep([string]$Python, [string[]]$ArgList, [string]$Name) {
    Write-Log ("开始 {0}: {1} {2}" -f $Name, $Python, ($ArgList -join " "))
    $out = & $Python @ArgList 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $out) { Write-Log "$line" }
    if ($code -ne 0) {
        Write-Log "$Name 退出码 $code" "WARN"
        return $false
    }
    Write-Log "$Name 完成"
    return $true
}

Write-Log "======== 每日采集开始 ========"

$python = Resolve-PythonPath
$dockerOk = Test-DockerEngine
if (-not $dockerOk) {
    $dockerOk = Start-DockerDesktopIfNeeded
}
$weweOk = $false
if ($dockerOk) {
    $weweOk = Start-WeweRssIfNeeded
}
$keyOk = Test-Path (Join-Path $PSScriptRoot ".env")

Write-Log ("Python: " + $(if ($python) { $python } else { "未找到" }))
Write-Log ("Docker 引擎: " + $(if ($dockerOk) { "就绪" } else { "未就绪" }))
Write-Log ("WeWe RSS http://127.0.0.1:4000: " + $(if ($weweOk) { "可访问" } else { "不可访问" }))
Write-Log (".env: " + $(if ($keyOk) { "存在" } else { "缺失" }))

if ($CheckOnly) {
    Write-Log "仅检查，不采集"
    if ($python -and $dockerOk) { exit 0 }
    exit 1
}

if (-not $python) {
    Write-Log "找不到 python，无法采集。请在能跑 wechat.py 的终端执行 setup_daily_task.ps1" "ERROR"
    exit 2
}

$failed = $false
if (-not $SkipWechat) {
    if (-not $weweOk) {
        Write-Log "跳过公众号：WeWe RSS 未就绪（Docker 未开或未扫码）" "WARN"
        $failed = $true
    } else {
        if (-not (Invoke-PythonStep $python @("wechat.py") "公众号简报")) {
            $failed = $true
        }
    }
}

if (-not $SkipWeb) {
    if (-not (Invoke-PythonStep $python @("crawl.py") "网页简报")) {
        $failed = $true
    }
}

if (-not (Invoke-PythonStep $python @("intel.py") "情报入库与知识库")) {
    Write-Log "intel.py 未成功，知识库可能未更新" "WARN"
}

Write-Log "合页入口: briefs\latest.html"
Write-Log "知识库: briefs\library\index.html"
Write-Log "局域网预览: python serve.py"
Write-Log "======== 每日采集结束 ========"
if ($failed) { exit 1 }
exit 0
