# mp3 외부 호스팅 로드맵

`episodes/` mp3 파일을 GitHub Pages artifact 에서 분리하기 위한 마이그레이션 계획.

## 왜 필요한가

| 시점 | docs/ artifact | 상태 |
|---|---:|---|
| 청소 전 (a47c201) | 2.02 GB | ⚠️ "Deployment might fail" 경고 + 1 GB 한도 2× 초과 |
| 청소 후 (d4e1cfb) | 1.34 GB | ⚠️ 같은 경고, 한도 0.34 GB 초과 |
| 에피소드 +50편 후 (예상) | ~1.7 GB | ❌ 한도 초과 폭 확대 — 언젠가 hard fail |

- GitHub Pages 의 `actions/upload-pages-artifact@v3` 는 **1 GB 권장 / 10 GB 하드 한도.** 1 GB 는 경고로 끝나지만 대형 배포가 점점 느려지고, 10 GB 임박 시 진짜 막힘.
- 평균 에피소드 ~7 MB. **현재 199편, 한 달에 ~10편 증가 추세** → 1년 후 +120편 ≈ +840 MB.
- mp3 가 git 에도 들어가 있어 `git clone` / `git fetch` / push 가 모두 무거움. 외부 호스팅 = 저장소 슬림화 + 배포 가속 + git 히스토리 부담 완화.

## 호스팅 후보 비교

| 옵션 | 비용 (1.5 GB 기준) | 설정 난이도 | 비고 |
|---|---|---|---|
| **Cloudflare R2** ⭐ | $0/월 (10 GB 까지 무료) | 중 | egress 무료, S3 호환, 커스텀 도메인 OK |
| GitHub Releases | $0 | 낮음 | 파일당 2 GB, 동일 provider, 다만 URL 이 release tag 종속이라 신규 업로드마다 release 갱신 |
| Backblaze B2 + Cloudflare CDN | ~$0.01/월 | 중 | Bandwidth Alliance 로 egress 무료, 셋업 단계가 R2 보다 많음 |
| AWS S3 | ~$0.04/월 + egress $0.09/GB | 중 | 인기 팟캐스트면 egress 비용 누적 — 비추 |
| 그대로 GitHub Pages | $0 | - | 10 GB 하드 한도까지 ~6년 버틸 수 있지만 계속 무거워짐 |

**권장: Cloudflare R2.** 비용·확장성·egress 정책 모두 팟캐스트 호스팅에 최적. 셋업도 1회성.

## 마이그레이션 단계

### Phase 0 — 셋업 (1회, ~30분)

- [ ] Cloudflare 계정 생성, R2 활성화
- [ ] 버킷 생성 (예: `notebooklm-podcast`)
- [ ] API token 발급 (Object Read & Write, 해당 버킷 한정)
- [ ] **공개 접근 설정** 결정: r2.dev 임시 URL vs 커스텀 도메인 (예: `audio.kiuk104.dev`)
  - 커스텀 도메인 권장: r2.dev URL 은 운영 용도 비추 (Cloudflare 정책)
- [ ] `config.yaml` 에 `r2:` 블록 추가 (endpoint, bucket, public_base_url)
- [ ] `requirements.txt` 에 `boto3` 추가 (S3 호환 SDK)

### Phase 1 — 코드 변경 (rss_generator.py 중심)

- [ ] `Episode.url` 을 `f"{r2_public_base}/{quote(self.path.name)}"` 로 변경
- [ ] `generate()` 의 docs/episodes 복사 루프 제거 (또는 옵션 플래그)
- [ ] 신규 함수 `upload_to_r2(path)` — 멱등성 (이미 있으면 skip, 크기/etag 비교)
- [ ] `generate()` 마지막에 episodes/ 의 신규 파일을 R2 에 업로드
- [ ] `docs/episodes/` 청소 sweep 은 그대로 두되 결국엔 디렉토리 자체 삭제

### Phase 2 — 일괄 업로드 (1회, ~10분)

- [ ] 스크립트 `scripts/migrate_to_r2.py` 작성 — `episodes/*.mp3` 전부 업로드
- [ ] checksum 검증 (로컬 SHA256 vs R2 ETag/메타데이터)
- [ ] 199개 × 7 MB ≈ 1.4 GB. R2 free tier 안에 들어감 (10 GB까지 무료, 이후 $0.015/GB/월)

### Phase 3 — 컷오버

- [ ] 로컬에서 `python src/rss_generator.py` 재실행 → feed.xml 의 enclosure URL 이 R2 로 바뀜
- [ ] git push (docs/feed.xml + docs/index.html 만 변경됨)
- [ ] **구독자 영향 0** — `<guid>` 가 여전히 파일명이라 같은 episode 로 인식, URL 만 교체
- [ ] 1주일 정도 검증 후 `docs/episodes/` 디렉토리 git rm + 커밋
- [ ] update.yml 의 `paths: docs/**` 트리거는 그대로 (feed.xml 변경 시에만 배포)

### Phase 4 — 신규 워크플로우 (자동화)

- [ ] [src/main.py](src/main.py) 가 다운로드 → episodes/ 저장 → **R2 업로드** → RSS 생성 순서로 동작
- [ ] [scripts/daily-update.ps1](scripts/daily-update.ps1) 은 변화 없음 (main.py 가 알아서 처리)
- [ ] R2 API token 을 환경변수 또는 `.env` (gitignore) 로 관리. CI 에는 secret 으로

### Phase 5 — 선택적 정리

- [ ] `episodes/` 를 git 에서 제외할지 결정:
  - **A안 (간단):** 그대로 두고 R2 와 이중 보관 (백업)
  - **B안 (슬림):** `.gitignore` 에 `episodes/` 추가 + `git-filter-repo` 로 history 청소. 저장소 ~1.4 GB 슬림화. 단, 클론 시 mp3 가 없어 R2 다운로드 스크립트 필요
- A안 이 백업·복구 단순함 — B안 은 진짜로 저장소가 무거워졌을 때만

## 리스크 / 영향

| 항목 | 평가 |
|---|---|
| 비용 | R2 free tier 안 (1.4 GB << 10 GB). 초과 시 $0.015/GB/월. 1년 후 +840 MB 이어도 여전히 0원 |
| 다운타임 | 거의 없음. feed.xml URL 만 바뀌고 구독자 GUID 동일 |
| 롤백 | `git revert` 로 feed.xml 되돌리면 즉시 Pages 로 복귀 (docs/episodes/ mp3 가 git history 에 남아있는 동안) |
| 종속성 | Cloudflare 계정 1개 추가, boto3 의존성 추가 |
| 보안 | R2 token 유출 시 누군가 버킷에 쓰기 가능 — token scope 를 해당 버킷 한정으로 |

## 비결정 사안 (마이그레이션 시작 전 답변 필요)

1. **커스텀 도메인** 사용? (Cloudflare DNS 에 audio 서브도메인 추가)
2. `episodes/` 를 git 에 계속 둘 것인가 (Phase 5 A안 vs B안)?
3. **분기점:** Pages artifact 가 1.5 GB / 2 GB / 5 GB 중 어디 도달했을 때 마이그레이션 시작? — 늦출수록 누적 업로드 분량은 늘지만 Phase 0~2 작업은 동일

## 참고 — 현 시점 측정값 (2026-05-01)

- `episodes/` 199 files, 1,388 MB
- `docs/episodes/` 199 files, 1,388 MB (청소 직후, 동기화 상태)
- artifact 업로드: 1,439,432,716 bytes ≈ 1.34 GB (Run 25211107054)
- 권장 한도까지 여유: ~340 MB ≈ 약 50편
