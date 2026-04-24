# NotebookLM 새 음성개요 → mp3 다운로드 → git push (→ GitHub Actions가 Pages 재배포)
# Windows 작업 스케줄러에서 매일 호출되는 스크립트입니다.

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("daily-update-{0}.log" -f (Get-Date -Format 'yyyyMMdd'))
Start-Transcript -Path $LogFile -Append | Out-Null

try {
    Write-Output ("[{0}] === daily-update start ===" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))

    $venvPy = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    $python = if (Test-Path $venvPy) { $venvPy } else { 'python' }
    Write-Output "[info] python: $python"

    Write-Output "[step] git pull --rebase --autostash"
    git pull --rebase --autostash origin main
    if ($LASTEXITCODE -ne 0) { throw "git pull --rebase 실패" }

    Write-Output "[step] main.py (NotebookLM 다운로드 + RSS 생성)"
    & $python src/main.py --config config.yaml
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "main.py 실패 (exit=$LASTEXITCODE)"
        Write-Warning "Google 세션 만료일 수 있습니다. 로컬에서 아래 명령으로 재로그인하세요:"
        Write-Warning "  $python src/downloader.py --login"
        throw "main.py 실패"
    }

    git add episodes/ docs/
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Output "[info] 변경사항 없음 — 커밋/푸시 생략"
        exit 0
    }

    $msg = "chore: daily update ({0})" -f (Get-Date -Format 'yyyy-MM-dd')
    git commit -m $msg
    if ($LASTEXITCODE -ne 0) { throw "git commit 실패" }

    Write-Output "[step] git push"
    git push
    if ($LASTEXITCODE -ne 0) { throw "git push 실패 — 'gh auth status'로 토큰 확인" }

    Write-Output "[done] push 완료 — GitHub Actions가 Pages를 재배포합니다."
}
catch {
    Write-Error $_
    exit 1
}
finally {
    Stop-Transcript | Out-Null
}
