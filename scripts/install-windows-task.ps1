param([string]$TaskName = 'NewsFetch-DailyAI')
$ErrorActionPreference = 'Stop'
$TaskRepo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$TaskPython = (Get-Command python.exe -ErrorAction Stop).Source
$TaskPythonW = Join-Path (Split-Path $TaskPython) 'pythonw.exe'
if (Test-Path -LiteralPath $TaskPythonW) { $TaskPython = $TaskPythonW }
# Windows 触发器每 5 分钟唤醒；程序自身使用固定 UTC+8 判断 08:00 日报。
$TaskAction = New-ScheduledTaskAction -Execute $TaskPython -Argument '-m news_fetch tick' -WorkingDirectory $TaskRepo
$TaskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$TaskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$TaskPrincipal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $TaskAction -Trigger $TaskTrigger -Settings $TaskSettings -Principal $TaskPrincipal -Description 'AI news: poll every 20 minutes; report daily at 08:00 Asia/Shanghai. Requires signed-in user.' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
