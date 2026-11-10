<#
.SYNOPSIS
    注册 litpush 每日自动运行计划任务（Windows 任务计划程序）。

.DESCRIPTION
    默认每天 08:30 以当前用户身份运行 scripts\run_daily.cmd。
    - 错过时间（比如当时关机）会在下次开机后补跑（StartWhenAvailable）
    - 电池供电时也运行
    - 单次最多跑 30 分钟

.PARAMETER Time
    每日运行时刻，24 小时制，例如 "08:30"、"07:15"。默认 08:30。

.PARAMETER TaskName
    任务名称，默认 "LitPush 每日文献推送"。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Time 07:30

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -RunNow
#>
param(
    [string]$Time = "08:30",
    [string]$TaskName = "LitPush 每日文献推送",
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root "scripts\run_daily.cmd"

if (-not (Test-Path $runner)) {
    Write-Error "找不到运行脚本：$runner"
    exit 1
}

if ($Time -notmatch '^\d{1,2}:\d{2}$') {
    Write-Error "时间格式应为 HH:mm，例如 08:30"
    exit 1
}

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$runner`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)

$who = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $who -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description "每日扫描文献并生成 Obsidian 速读卡片" -Force | Out-Null

Write-Host "已注册计划任务：$TaskName" -ForegroundColor Green
Write-Host "  运行时刻：每天 $Time"
Write-Host "  执行命令：$runner"
Write-Host "  工作目录：$root"
Write-Host ""

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host ("下一次运行：" + $info.NextRunTime)
Write-Host ""
Write-Host "常用操作："
Write-Host "  立即试跑：Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  查看状态：Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "  删除任务：powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1"

if ($RunNow) {
    Write-Host ""
    Write-Host "正在立即执行一次..."
    Start-ScheduledTask -TaskName $TaskName
}
