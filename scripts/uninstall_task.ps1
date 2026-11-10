<#
.SYNOPSIS
    删除 litpush 的每日计划任务。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1
#>
param(
    [string]$TaskName = "LitPush 每日文献推送"
)

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "没有找到计划任务：$TaskName" -ForegroundColor Yellow
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "已删除计划任务：$TaskName" -ForegroundColor Green
