# 註冊每日排程：每天 06:00（本機時間，請確認電腦時區為台北）執行 run_daily.cmd。
# 電腦在 06:00 關機或睡眠時，開機後會補跑一次（StartWhenAvailable）。
# 移除：Unregister-ScheduledTask -TaskName "badminton-daily-agent" -Confirm:$false
$root = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "$root\scripts\run_daily.cmd" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 6:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "badminton-daily-agent" -Action $action -Trigger $trigger -Settings $settings `
    -Description "羽球日報：每日收集賽果、新聞、排名並推送 Discord 摘要" -Force
