# 용량 관리 — 두 가지 Plan

GitHub Pages artifact 가 1 GB 임계를 넘으면 (`docs/` 합계 기준) 배포 경고가 뜨고, 10 GB hard limit 까지 도달하면 진짜 막힘. 이를 다루는 두 가지 전략.

## 현 상황 (2026-05-01)

- `episodes/`: 199 files, **1,388 MB** (모두 mp3 64k mono — [src/downloader.py:349](src/downloader.py#L349) 의 자동 transcode 로 이미 압축 끝)
- `docs/episodes/`: 199 files, 1,388 MB (방금 d4e1cfb 에서 청소 + sync 완료)
- artifact 업로드: 1.34 GB → "Deployment might fail" 경고
- 임계까지 여유: ~340 MB ≈ **약 50편**

평균 7 MB/episode 가 **64k mono mp3 의 사실상 floor** — 더 압축하면 한국어 음성 명료도가 손상. 즉 추가 transcode 로는 못 줄임. **에피소드 수를 관리하는 게 유일한 레버.**

---

## Plan A — Transcode + Retention (현재 권장)

> **상태:** transcode 는 이미 적용됨 ✓ / retention 추가 필요

### A-1. Transcode (이미 동작 중)

[src/downloader.py](src/downloader.py) 가 다운로드 직후 m4a → mp3 64k mono 로 변환 + 원본 m4a 삭제. config.yaml 의 `transcode_to_mp3: true` 가 토글. 이미 199편 모두 적용됨.

기존 m4a 가 있는 경우 일괄 변환:
```bash
python src/transcode_episodes.py
```

### A-2. Retention (추가 필요)

용량 기반 rolling window:
- `docs/episodes/` 합계가 N MB 넘으면 가장 오래된 에피소드부터 prune
- `feed.xml` 에서도 동일하게 제외
- `episodes/` (소스) 는 그대로 — 백업 / 추후 복원 가능

config.yaml 추가될 키:
```yaml
retention:
  max_total_mb: 900   # docs/episodes/ 합계 상한 (default 비활성)
  # 향후 추가 예정: max_items, max_age_days
```

900 MB 권장 이유: 1 GB 경고선 아래 + 신규 push 1편 (~7 MB) 마진. 사용자 원하는 만큼 줄이거나 늘릴 수 있음.

### Plan A 의 손익

| | + | − |
|---|---|---|
| 비용 | $0 | - |
| 셋업 | retention 코드만 추가 | - |
| 다운타임 | 없음 | - |
| 청취 경험 | 늘 최신 N편 RSS | 오래된 에피소드는 RSS 에서 사라짐 (소스는 보존) |
| 신규 인프라 | 없음 | - |

NotebookLM 음성개요는 보통 "지금 공부 중인 자료" 의 요약이라 6개월~1년 후 다시 들을 일이 적음 → rolling window 가 자연스러움.

### Plan A 의 한계

- 1편이 100 MB 초과 (드물지만 가능 — 매우 긴 음성개요) 시 GitHub blob 한계 도달. 그땐 Plan B 필요.
- 사용자가 모든 과거 에피소드를 RSS 에 노출하고 싶은 경우 부적절.

---

## Plan B — 외부 호스팅 (헤비 유저 / 미래용)

> **상태:** 미래 옵션. Plan A 가 막히는 케이스를 위한 프리미엄 노선.

### 언제 Plan B 가 필요한가

| 트리거 | Plan A 가 못 잡는 이유 |
|---|---|
| 1편이 100 MB 초과 | GitHub blob hard limit |
| 모든 과거 에피소드 영구 보존 + RSS 노출 | retention 으로 못 잘라냄 |
| 월 100편+ 추가하는 헤비 유저 | retention 으로 따라잡으려면 청취 가능 윈도우가 너무 좁아짐 |
| 공개 repo 가 부담 (민감 자료) | GitHub Pages 가 public 노출 |
| Egress 비용 / CDN 분리가 필요 | GitHub Pages 트래픽 정책 한계 |

### 후보 비교 (1.5 GB 기준)

| 옵션 | 비용/월 | 셋업 | 비고 |
|---|---|---|---|
| **Cloudflare R2** ⭐ | $0 (10 GB 까지 무료) | 중 | egress 무료, S3 호환, 커스텀 도메인 |
| GitHub Releases | $0 | 낮음 | 파일당 2 GB, release tag 종속 |
| Backblaze B2 + CF CDN | ~$0.01 | 중 | Bandwidth Alliance 로 egress 무료 |
| AWS S3 | ~$0.04 + egress $0.09/GB | 중 | egress 누적 시 부담 |

### 마이그레이션 순서 (Plan B 진행 시)

1. **셋업** (~30분): R2 버킷 + API token + (선택) 커스텀 도메인. config.yaml 에 r2 블록 추가, requirements.txt 에 boto3.
2. **코드 변경**: `Episode.url` 을 `f"{r2_public_base}/{quote(name)}"` 로. docs/episodes 복사 단계 제거.
3. **일괄 업로드** (~10분): `scripts/migrate_to_r2.py` — episodes/ 의 모든 mp3 를 R2 로 PUT + checksum 검증.
4. **컷오버**: feed.xml 재생성 → push. 구독자 GUID 동일 → URL 만 교체 → 다운타임 0.
5. **자동화**: src/main.py 가 다운로드 직후 R2 업로드 → RSS 생성. daily-update.ps1 변화 없음.

### Plan B 의 손익

| | + | − |
|---|---|---|
| 비용 | R2 free tier 안 ($0) | Cloudflare 계정 1개 추가 |
| 모든 에피소드 영구 보존 | ✓ | retention 안 해도 됨 |
| 100 MB+ 초과 파일 | ✓ | - |
| 다운타임 | 0 (URL 만 교체, GUID 동일) | - |
| 종속성 | - | 외부 서비스 1개 |

### Plan B 진행 전 결정 필요한 것

1. 커스텀 도메인 vs r2.dev URL (운영용은 커스텀 권장)
2. `episodes/` 를 git 에 계속 둘지 (백업) vs `.gitignore` + git-filter-repo 로 history 청소
3. **분기점**: artifact 가 어디 도달했을 때 마이그레이션 시작? — 현재 1.34 GB 가 1.5 / 2 / 5 GB 어디?

---

## 의사결정 프레임

```
artifact 1 GB 초과 시작?
└─ 예 → 모든 과거 에피소드 RSS 에 보존이 중요?
   ├─ 아니오 → Plan A (transcode + retention)  ← 현재 99% 사용자
   └─ 예    → Plan B (외부 호스팅)              ← 헤비 유저
```

**현재 본인 = Plan A 로 충분.** Plan B 는 청취 패턴이 "역대 모든 에피소드 listen 가능해야 함" 으로 바뀌거나, 1편이 100 MB 초과되거나, 공개 repo 가 부담스러워질 때 다시 검토.
