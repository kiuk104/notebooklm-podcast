# Windows 작업 스케줄러에 daily-update.ps1을 매일 09:00 실행으로 등록합니다.
# 사용법 (관리자 권한 없이 사용자 계정으로 실행):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\register-scheduled-task.ps1
#
# 해제:
#   Unregister-ScheduledTask -TaskName 'notebooklm-podcast-daily' -Confirm:$false

$ErrorActionPreference = 'Stop'

$RepoRoot  = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $RepoRoot 'scripts\daily-update.ps1'

if (-not (Test-Path $ScriptPath)) {
    throw "daily-update.ps1을 찾을 수 없습니다: $ScriptPath"
}

$Action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`"" `
    -WorkingDirectory $RepoRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At 9am

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

# Interactive: 사용자가 로그인돼 있을 때만 실행 (비밀번호 저장 불필요)
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName 'notebooklm-podcast-daily' `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description 'NotebookLM 음성개요 다운로드 + RSS push (매일 09:00)' `
    -Force | Out-Null

Write-Output "[OK] 작업 스케줄러에 'notebooklm-podcast-daily' 등록됨 (매일 09:00)"
Write-Output "확인: Get-ScheduledTask -TaskName 'notebooklm-podcast-daily'"
Write-Output "즉시 테스트: Start-ScheduledTask -TaskName 'notebooklm-podcast-daily'"
