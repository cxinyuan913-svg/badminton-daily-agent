# 註冊兩個排程（本機時間，電腦時區須為台北）：
#   badminton-daily-agent  每天 06:00：收集＋晨報（新聞、IC／IS 精選、週一暱稱週報、漏發提醒）
#   badminton-watch        每 30 分鐘：每站當地當天打完就發（brief.watch）
# 電腦在排定時間關機或睡眠時，開機後會補跑一次（StartWhenAvailable）。
# 移除：Unregister-ScheduledTask -TaskName "badminton-daily-agent","badminton-watch" -Confirm:$false
$root = Split-Path -Parent $PSScriptRoot
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

$daily = New-ScheduledTaskAction -Execute "$root\scripts\run_daily.cmd" -WorkingDirectory $root
Register-ScheduledTask -TaskName "badminton-daily-agent" -Action $daily -Trigger (New-ScheduledTaskTrigger -Daily -At 6:00am) `
    -Settings $settings -Description "羽球日報：每日收集與晨報" -Force

$watch = New-ScheduledTaskAction -Execute "$root\scripts\run_watch.cmd" -WorkingDirectory $root
$every30 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "badminton-watch" -Action $watch -Trigger $every30 `
    -Settings $settings -Description "羽球日報：每站當地當天打完就發" -Force
