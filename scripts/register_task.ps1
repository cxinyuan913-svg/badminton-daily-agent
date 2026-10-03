# 註冊所有 badminton-* 排程（本機時間，電腦時區須為台北；所有排程都在本機，notes 10-02 06:35）：
#   badminton-backup        每天 04:00：data\brief.db 一致備份到 data\backups\，保留 7 份
#   badminton-daily-agent   每天 06:00：收集＋晨報（新聞、IC／IS 精選、週一週報、漏發提醒）
#   badminton-watch         每 30 分鐘：每站當地當天打完就發（brief.watch）＋新聞收集
#   badminton-fbpost        每天 18:00：補抓排名 → 大賽賽前看點 → 粉專貼文 → 影片腳本 1 支（10-03 Raymond：固定產出統一晚上 6 點）
#   badminton-offsite       每週一 12:00：最新備份 gzip 推到主機 bda-vultr（異地備份）
#   badminton-backfill-bwf  每小時：BWF 新聞回補（可續跑，做完就直接結束）
#   badminton-backfill-tsna 每小時：TSNA 新聞回補（可續跑，做完就直接結束）
#
# 背景執行、不跳視窗（notes 10-02 17:05）：
#   預設：「不論使用者是否登入都執行」（S4U，不存密碼）。**需要系統管理員權限的 PowerShell**：
#       powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
#   備案：S4U 跑不起來時（.env、網路或 Python 路徑有問題），用 conhost --headless 包住 .cmd，不需要系統管理員：
#       powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Headless
# 共通：錯過時盡快執行（StartWhenAvailable）＋喚醒電腦以執行（WakeToRun）。
# 移除：Get-ScheduledTask -TaskName "badminton-*" | Unregister-ScheduledTask -Confirm:$false
param([switch]$Headless)

$root = Split-Path -Parent $PSScriptRoot
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$longSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Days 7) -MultipleInstances IgnoreNew
if ($Headless) {
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U
}

function New-Action([string]$cmd, [string]$arguments = "") {
    $script = Join-Path $root "scripts\$cmd"
    if ($Headless) {
        return New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless cmd.exe /c `"`"$script`" $arguments`"" -WorkingDirectory $root
    }
    return New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$script`" $arguments`"" -WorkingDirectory $root
}

function Register([string]$name, $action, $trigger, $set, [string]$desc) {
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $set -Principal $principal `
        -Description $desc -Force | Out-Null
    "registered $name"
}

Register "badminton-backup" (New-Action "run_backup.cmd") (New-ScheduledTaskTrigger -Daily -At 4:00am) $settings "badminton: daily DB backup"
Register "badminton-daily-agent" (New-Action "run_daily.cmd") (New-ScheduledTaskTrigger -Daily -At 6:00am) $settings "badminton: daily collection and morning report"
$every30 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30)
Register "badminton-watch" (New-Action "run_watch.cmd") $every30 $settings "badminton: send each event when its local day finishes"
Register "badminton-fbpost" (New-Action "run_fbpost.cmd") (New-ScheduledTaskTrigger -Daily -At 6:00pm) $settings "badminton: rankings, FB page post and video script at 18:00"
Register "badminton-offsite" (New-Action "run_offsite.cmd") (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 12:00pm) $settings "badminton: weekly offsite backup to host"
foreach ($src in "bwf", "tsna") {
    $hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Hours 1)
    Register "badminton-backfill-$src" (New-Action "run_backfill_news.cmd" $src) $hourly $longSettings "badminton: news backfill $src (resumable)"
}
