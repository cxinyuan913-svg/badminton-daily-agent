# 註冊五個排程（本機時間，電腦時區須為台北；所有排程都在本機，notes 10-02 06:35）：
#   badminton-backup       每天 04:00：data\brief.db 一致備份到 data\backups\，保留 7 份
#   badminton-daily-agent  每天 06:00：收集＋晨報（新聞、IC／IS 精選、週一暱稱週報、漏發提醒）
#   badminton-watch        每 30 分鐘：每站當地當天打完就發（brief.watch）＋新聞收集
#   badminton-fbpost       每天 07:00：粉專貼文草稿（FBPOST=1 才推，預設只寫檔）
#   badminton-offsite      每週一 12:00：最新備份 gzip 推到主機 bda-vultr（異地備份，主機保留 8 份）
# 錯過時盡快執行（StartWhenAvailable）＋喚醒電腦以執行（WakeToRun）。
# 移除：Unregister-ScheduledTask -TaskName "badminton-daily-agent","badminton-watch","badminton-fbpost","badminton-backup","badminton-offsite" -Confirm:$false
$root = Split-Path -Parent $PSScriptRoot
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

$backup = New-ScheduledTaskAction -Execute "$root\scripts\run_backup.cmd" -WorkingDirectory $root
Register-ScheduledTask -TaskName "badminton-backup" -Action $backup -Trigger (New-ScheduledTaskTrigger -Daily -At 4:00am) `
    -Settings $settings -Description "羽球日報：資料庫每日備份" -Force

$daily = New-ScheduledTaskAction -Execute "$root\scripts\run_daily.cmd" -WorkingDirectory $root
Register-ScheduledTask -TaskName "badminton-daily-agent" -Action $daily -Trigger (New-ScheduledTaskTrigger -Daily -At 6:00am) `
    -Settings $settings -Description "羽球日報：每日收集與晨報" -Force

$watch = New-ScheduledTaskAction -Execute "$root\scripts\run_watch.cmd" -WorkingDirectory $root
$every30 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "badminton-watch" -Action $watch -Trigger $every30 `
    -Settings $settings -Description "羽球日報：每站當地當天打完就發" -Force

$fbpost = New-ScheduledTaskAction -Execute "$root\scripts\run_fbpost.cmd" -WorkingDirectory $root
Register-ScheduledTask -TaskName "badminton-fbpost" -Action $fbpost -Trigger (New-ScheduledTaskTrigger -Daily -At 7:00am) `
    -Settings $settings -Description "羽球日報：粉專貼文草稿" -Force

$offsite = New-ScheduledTaskAction -Execute "$root\scripts\run_offsite.cmd" -WorkingDirectory $root
Register-ScheduledTask -TaskName "badminton-offsite" -Action $offsite -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 12:00pm) `
    -Settings $settings -Description "羽球日報：每週異地備份到主機" -Force
