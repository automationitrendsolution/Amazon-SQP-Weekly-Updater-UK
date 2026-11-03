# Registers the Search Query Details (competitor) scraping, Seller Central UK, in Windows Task Scheduler (this PC, current user).
#
#   powershell -ExecutionPolicy Bypass -File deploy\install_competitors_task.ps1            # install / update
#   powershell -ExecutionPolicy Bypass -File deploy\install_competitors_task.ps1 -Remove    # remove
#
# Runs `run.py --weekly-scrape --quiet` every day at 15:30, 18:30 and 21:30 (this PC's time, IST = UK business
# hours). Each part opens up to 400 Search Query Details pages of the oldest week not fully saved (from
# SC_COMPETITORS_FROM), then loads that week's saved pages into powerbidb.BrandAnalytics_Competitor_UK (no duplicates).
# A part stops by itself on a captcha / warning / "Access Required" page, or when the saved Seller Central
# sign-in has expired (it never logs in, so no OTP is sent): run `run.py --sc-login` once, the next part continues.
# Needs the PC on and the user signed in to Windows (screen may be locked). Chrome runs hidden (no window).
# A part still running when the next is due is left alone; missed parts (PC off) are not started later in the day.
param([switch]$Remove)

$TaskName = "Amazon SQP Competitors UK"
$Project = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Project ".venv\Scripts\pythonw.exe"   # pythonw: no console window

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed '$TaskName'."
    return
}
if (-not (Test-Path $Python)) { throw "Python not found at $Python (create the .venv first)" }

$action = New-ScheduledTaskAction -Execute $Python -Argument "-B run.py --weekly-scrape --quiet" -WorkingDirectory $Project
$triggers = @(
    New-ScheduledTaskTrigger -Daily -At 15:30
    New-ScheduledTaskTrigger -Daily -At 18:30
    New-ScheduledTaskTrigger -Daily -At 21:30
)
$settings = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable -WakeToRun -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3 -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
    -Description "Brand Analytics Search Query Details (Seller Central) -> powerbidb.BrandAnalytics_Competitor_UK: 3 parts a day, 400 pages each" -Force | Out-Null
Write-Output "Registered '$TaskName': daily 15:30, 18:30, 21:30, $Python -B run.py --weekly-scrape --quiet (in $Project)"
