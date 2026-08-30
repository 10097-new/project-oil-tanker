# 一次性注册 Windows 任务计划：工作日/每天 12:10 跑 run_daily.ps1。
# 在「已经能手动 python wechat.py」的同一终端执行：
#   powershell -ExecutionPolicy Bypass -File .\setup_daily_task.ps1
# 取消:
#   powershell -ExecutionPolicy Bypass -File .\setup_daily_task.ps1 -Remove

param(
    [string]$At = "12:10",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$taskName = "ShippingNewsDailyBrief"
$runScript = Join-Path $PSScriptRoot "run_daily.ps1"
$logsDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已删除任务 $taskName"
    exit 0
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "当前终端找不到 python。请在能手动跑采集的环境里执行本脚本。"
}
Set-Content -Path (Join-Path $logsDir "python_path.txt") -Value $python -Encoding UTF8
Write-Host "已记录 Python: $python"

$hour, $minute = $At.Split(":")
$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour ([int]$hour) -Minute ([int]$minute) -Second 0)
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`"" `
    -WorkingDirectory $PSScriptRoot
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "航运制裁简报：公众号 + 英文网页。需已登录、Docker 可被脚本拉起。" `
    -Force | Out-Null

Write-Host ""
Write-Host "已注册任务 $taskName，每天 $At 运行。"
Write-Host "立刻试跑:  Start-ScheduledTask -TaskName $taskName"
Write-Host "看日志:    Get-Content .\logs\daily_$(Get-Date -Format yyyy-MM-dd).log -Tail 40"
Write-Host ""
Write-Host "仍需你手动保证（脚本无法代替）:"
Write-Host "  1. 到点电脑是开机或睡眠可唤醒，不要关机"
Write-Host "  2. 已登录 Windows（任务按当前用户交互运行，方便拉起 Docker Desktop）"
Write-Host "  3. Docker Desktop 已安装；建议在 Docker 设置打开 Start when you log in"
Write-Host "  4. 浏览器打开 http://127.0.0.1:4000 ，微信读书已扫码，不要勾 24 小时退出"
Write-Host "  5. 名单里的公众号已在 WeWe 里添加成功"
Write-Host "  6. 梯子在采集前挂上（网页源如 Maritime Executive；Docker 未必走梯子）"
Write-Host "  7. .env 里 DeepSeek 密钥仍有效"
Write-Host ""
Write-Host "外出用手机看、不依赖这台电脑：还要把 briefs 静态页发到网上，下一步再做。"
