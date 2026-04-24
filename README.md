# NotebookLM → 개인 팟캐스트 RSS

NotebookLM의 음성개요(Audio Overview)를 자동으로 다운로드해서 RSS 피드를 만들고, GitHub Pages에 호스팅해서 본인 팟캐스트 앱(Apple Podcasts, Pocket Casts, Overcast 등)에서 구독해 듣기 위한 개인용 도구입니다.

> ⚠️ NotebookLM에는 공식 API가 없어서 **브라우저 자동화(Playwright)** 로 다운로드합니다. UI가 바뀌면 셀렉터를 손봐야 할 수 있어요.

## 전체 흐름

```
┌──────────────┐  Playwright   ┌────────────┐  generate   ┌──────────┐
│  NotebookLM  │ ────────────▶ │ episodes/  │ ──────────▶ │ feed.xml │
│  (browser)   │   download    │   *.mp3    │   RSS gen   │          │
└──────────────┘               └────────────┘             └──────────┘
                                      │                         │
                                      └────────┬────────────────┘
                                               ▼
                                    ┌─────────────────────┐
                                    │  GitHub Pages 배포  │
                                    │  (docs/ 폴더)       │
                                    └─────────────────────┘
                                               │
                                               ▼
                                  https://<user>.github.io/<repo>/feed.xml
                                  ↑ 이 URL을 팟캐스트 앱에 등록
```

## 호스팅을 GitHub Pages로 추천한 이유

- **무료**, 공개 repo면 트래픽 제한 없음
- 파일당 100MB / repo 1GB 제한이 있는데, NotebookLM 음성개요는 보통 10~30MB라 수십 편은 충분
- HTTPS 자동, 팟캐스트 앱들이 잘 인식
- GitHub Actions와 자연스럽게 연결돼서 **스케줄 자동화**까지 한 곳에서 처리됨

> 음성개요에 민감한 내부 자료가 들어간다면 public repo가 부담스러울 수 있습니다. 그 경우엔 Cloudflare R2 + 별도 RSS 호스팅(예: Vercel)을 추천해요. 필요하면 그 버전도 만들어드릴게요.

## 폴더 구조

```
notebooklm-podcast/
├── README.md
├── requirements.txt
├── config.example.yaml         # 설정 템플릿 (복사해서 config.yaml로 사용)
├── src/
│   ├── downloader.py           # Playwright로 NotebookLM 음성개요 다운로드
│   ├── rss_generator.py        # episodes/ → feed.xml 생성
│   └── main.py                 # 다운로드 + RSS 생성 오케스트레이션
├── episodes/                   # 다운로드된 mp3 (커밋해서 GitHub Pages로 서빙)
├── docs/                       # GitHub Pages 루트
│   ├── feed.xml
│   ├── index.html              # 사람이 보기 좋은 에피소드 목록
│   └── episodes/               # mp3 심볼릭/복사본
├── .auth/                      # 브라우저 세션(쿠키) — .gitignore 필수
└── .github/workflows/
    └── update.yml              # 매일 새 음성개요 체크 + 배포
```

## 1회만 하면 되는 셋업

### 1. 로컬에 클론

```bash
git clone <your-repo>
cd notebooklm-podcast
python -m venv .venv && source .venv/bin/activate   # Windows는 .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. 설정 파일 만들기

```bash
cp config.example.yaml config.yaml
```

`config.yaml`을 열어서 다음을 채웁니다:

```yaml
podcast:
  title: "내 NotebookLM 팟캐스트"
  author: "기욱"
  description: "NotebookLM에서 만든 음성개요 모음"
  base_url: "https://<your-username>.github.io/<repo-name>"

notebooks:
  - id: "abc123def456"      # NotebookLM URL의 노트북 ID
    name: "AI 논문 정리"
  - id: "xyz789..."
    name: "회사 위키"
```

> 노트북 ID는 `https://notebooklm.google.com/notebook/<여기>` 부분입니다.

### 3. 첫 로그인 (1회만)

```bash
python src/downloader.py --login
```

크로미움 창이 뜨면 평소처럼 Google 로그인 → NotebookLM이 보이면 그 창을 그냥 닫습니다. 세션은 `.auth/` 폴더에 저장됩니다. 이후엔 자동 실행 시 다시 로그인할 필요 없어요.

### 4. 처음 한 번 수동 실행

```bash
python src/main.py
```

`episodes/`에 mp3가, `docs/feed.xml`이 생성되면 git에 커밋하고 push:

```bash
git add episodes docs
git commit -m "Initial episodes"
git push
```

### 5. GitHub Pages 활성화

#### 5-A. `gh` CLI로 자동 설정 (추천)

저장소까지 한 번에 만들고 Pages/Actions 권한까지 설정하는 방법입니다. `gh` CLI가 설치돼 있어야 합니다.

```bash
# 최초 1회 로그인
gh auth login            # → Login with a web browser 선택

# 로컬에 origin이 설정돼 있지 않다면 저장소 생성과 함께
gh repo create <owner>/<repo> --public \
  --description "NotebookLM 음성개요 팟캐스트"

# 첫 푸시
git push -u origin main

# Pages 빌드 소스를 "GitHub Actions"로 지정
gh api -X POST repos/<owner>/<repo>/pages -f build_type=workflow
# 이미 활성화돼 있으면 POST 대신 PUT 사용

# 워크플로우에 쓰기 권한 부여 (update.yml의 git push 스텝에 필수)
gh api -X PUT repos/<owner>/<repo>/actions/permissions/workflow \
  -f default_workflow_permissions=write \
  -F can_approve_pull_request_reviews=false
```

몇 분 뒤 `https://<owner>.github.io/<repo>/feed.xml`이 응답합니다. 워크플로우 성공 여부는 `gh run list --repo <owner>/<repo>` 또는 `gh run watch`로 확인하세요.

#### 5-B. GitHub 웹 UI로 수동 설정

- Settings → **Pages** → Source를 **"GitHub Actions"**로 선택 (이 저장소는 `docs/` 폴더를 브랜치에서 직접 서빙하지 않고, `.github/workflows/update.yml`이 `docs/`를 artifact로 올려 배포합니다)
- Settings → **Actions** → General → Workflow permissions → **"Read and write permissions"** 선택

### 6. 팟캐스트 앱에 등록

- **Apple Podcasts**: 라이브러리 → 팟캐스트 → URL로 추가
- **Pocket Casts**: 검색창에 RSS URL 직접 입력
- **Overcast**: + → URL 추가

## 자동화

`.github/workflows/update.yml`이 매일 한 번 실행되면서 새 음성개요를 체크합니다. 

⚠️ 단, **GitHub Actions에서 Google 로그인을 자동화하는 건 위험**합니다(2FA, 캡챠, 보안 정책 위반). 추천 패턴은:

1. **로컬 cron으로 실행** (가장 안정) → 결과만 git push
2. 또는 **세션 쿠키를 GitHub Secrets에 저장** (만료되면 갱신 필요, 자세한 건 워크플로우 파일 주석 참조)

## 트러블슈팅

| 증상 | 원인 / 대응 |
|---|---|
| 다운로드 버튼을 못 찾음 | NotebookLM UI 업데이트. `src/downloader.py` 셀렉터 수정 |
| 로그인이 풀림 | `.auth/` 삭제 후 `--login` 다시 실행 |
| RSS가 앱에서 인식 안 됨 | https://podba.se/validate 에서 검증 |
| 새 에피소드가 추가 안 됨 | `episodes/` 파일명이 `YYYYMMDD__노트북명__제목.mp3` 패턴인지 확인 (구분자는 언더바 2개) |
| 팟캐스트 앱이 mp3 URL을 못 읽음 | 엔클로저 URL이 percent-encoding 됐는지 확인 — 한글 파일명이면 `rss_generator.py`의 `urllib.parse.quote`가 처리합니다 |

## 라이선스 / 주의

NotebookLM 콘텐츠는 본인 자료에서 생성된 것을 본인이 듣는 용도로만 사용하세요. **공개 팟캐스트로 배포하려면 원본 자료의 저작권을 직접 확인해야 합니다.**
