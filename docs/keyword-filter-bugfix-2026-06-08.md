# biz-monitor 검색어·제외어 필터 결함 수정 (2026-06-08)

`docs/keyword-filter-policy-2026-06-08.md` 정책 변경 이후 발견된 결함과 2차 코드 검토(수정파일.md)로 추가 발견된 구조적 문제를 합산해 수정한 내역이다. 핵심은 **문서가 "통과 예시"로 명시한 양식·통합관리 공고가 실제로는 차단되던 버그**와 **수동 검색이 정기 스케줄러와 다른 필터 경로를 사용하는 구조적 불일치**다.

---

## 배경 / 문제

정책 적용 후 코드 추적·실행 재현으로 다음을 확인했다.

- `is_broad_compound_token()`이 **부분문자열(substring) 매칭**이라, 광역어 `양식`이 거의 모든 양식 도메인 키워드(`육상양식`, `스마트양식`, `양식장 통합관리` …)의 부분문자열이 되어 supporting 키워드가 전멸 → `scheduler._has_additional_site_keyword_match` 2차 게이트에서 차단.
- 실행 재현: `육상양식장 통합관리 시스템`(`양식` 검색) → `broad_compound_without_site_keyword`로 **차단**.

---

## 수정 항목

### 1차 수정 (주요 버그)

| # | 심각도 | 내용 | 파일 |
|---|---|---|---|
| 1 | 🔴 | `is_broad_compound_token` 부분문자열 → **정확일치** 매칭 | `app/services/broad_search_terms.py` |
| 2 | 🔴 | 동작불능 `AI + 기상/해양/양식` 검색어 3개 제거 + DB 동기화 | `.env.override`, `.env.example`, `site_keywords` 테이블 |
| 3 | 🟠 | 복합 검색어 9개 라이브 검증 → **유지**(근거 문서화) | (변경 없음, 문서 기록) |
| 4 | 🟠 | 제외어 매칭을 본문 전체 → **제목 한정**으로 축소 | `app/services/business_scope_filter.py` |
| 5 | 🟠 | AI 구제 게이트 → #1로 자연 해소(코드 변경 없음) | (확인만) |
| 6 | 🟡 | 미사용 죽은 함수 `is_broad_compound_search_term` 제거 | `app/services/broad_search_terms.py` |
| 7 | 🟡 | 빈 잔여 디렉토리 제거 | `appservices/`, `apprepositories/`, `appweb_static/` |
| 8 | 🟡 | 정책 문서 갱신 | `docs/keyword-filter-policy-2026-06-08.md` |

### 2차 수정 (구조적 불일치 — `수정파일.md` 검토 기반)

| # | 심각도 | 내용 | 파일 |
|---|---|---|---|
| A | 🟠 | **2차 게이트를 env → DB 활성 검색어 기준으로 변경** | `app/services/scheduler.py` |
| B | 🟠 | **수동 검색(pipeline.py)에 제외어 필터 추가** | `app/services/pipeline.py` |
| C | 🟡 | pipeline.py의 `settings` 중복 할당 제거 (루프 밖으로 이동) | `app/services/pipeline.py` |
| D | 🟡 | 회귀 테스트 보강 (DB 게이트·pipeline 제외어) | `tests/test_scheduler_policy.py`, `tests/test_pipeline_scope_filter.py` |

### #1 광역어 토큰 판별 — 부분문자열 → 정확일치

```python
# Before (substring 매칭): '양식' in '육상양식' == True → 육상양식이 광역토큰으로 오인되어 제거
return any(term in normalized for term in BROAD_COMPOUND_ONLY_TERMS)

# After (정확일치): '양식'만 광역토큰, '육상양식'은 supporting으로 보존
return normalized in {
    normalize_text(term).lower() for term in BROAD_COMPOUND_ONLY_TERMS
}
```

단일 수정으로 두 경로가 동시에 정상화된다.
- `filter_supporting_keyword_matches()` (relevance.py) — supporting 키워드에서 `육상양식` 등 보존
- `_has_additional_site_keyword_match()` (scheduler.py:340) — 2차 게이트에서 `육상양식`/`양식장 통합관리`를 유효 키워드로 인정

### #2 동작불능 `AI + *` 검색어 제거

`AI + 기상`, `AI + 해양`, `AI + 양식`은 g2b 공고명 검색(`bidPbancNm`)에 `" + "` 포함 문자열을 리터럴로 넣어 항상 0건이었다. env에서 제거 후 `sync_site_keywords`로 DB 반영 → g2b 활성 검색어 **53 → 50개**. (`AI + *` 3개 row는 `enabled=0`)

### #3 복합 검색어 9개 — 라이브 검증 후 유지

라이브 g2b 단건 호출 결과(2026-06-08):

| 검색어 | 수집 |
|---|---|
| `해양기상`(단일, 대조) | 2건 |
| `해양기상 유지보수` | 0건 |
| `수산 양식` | 0건 |
| `기상 통합관리` | 0건 |
| `양식장 통합관리` | 1건 |

g2b는 공백을 AND가 아닌 **리터럴 substring**으로 검색하므로 복합어는 대부분 0건. 의도는 광역 단독어(`유지보수`/`통합관리`/`양식`) + 도메인 키워드 동반 검증으로 이미 커버되나, 리터럴 일치 공고(예: `양식장 통합관리` 1건)를 놓치지 않기 위한 **안전망으로 유지** 결정.

### #4 제외어 매칭 — 제목 한정

기존엔 제목 + `raw_payload`(본문 전체)를 합쳐 substring 검사 → `공원`·`차량`·`이온` 같은 짧은 단어가 본문 어디든 있으면 공고 전체가 과잉 제외됐다. `_candidate_scope_text`를 **제목(`candidate.title`)만** 검사하도록 변경.

### #6 / #7 정리

- `is_broad_compound_search_term()`: `if` 조건 때문에 substring 분기가 죽어 `is_exact`와 동일 동작 + 미사용 → 삭제.
- `appservices/`·`apprepositories/`·`appweb_static/`: 빈 폴더(오타성 잔재) → 삭제.

---

## 2차 수정 상세 (수정파일.md 검토 기반)

### A. 2차 게이트 — env → DB 활성 검색어 기준

`scheduler.py`의 광역어 2차 게이트가 프로세스 시작 시 `.env`에서 1회 로드된 `settings.site_keywords`(env 기반)를 사용했다. Slack 명령이나 `sync_site_keywords`로 DB를 변경해도 재시작 전까지 stale 상태가 유지되는 구조적 불일치.

```python
# Before: env 기반 (stale 가능)
site_keywords = list(getattr(settings, "site_keywords", {}).get(job.site_code, []))

# After: DB 활성 검색어 기준 (항상 최신)
site_keywords = [kw for sc, kw in list_enabled_site_keywords(session) if sc == job.site_code]
```

`list_enabled_site_keywords`는 이미 `scheduler.py`에 import되어 있었고, `process_job`에 `session`이 이미 있으므로 단 한 줄 교체로 완료.

### B. pipeline.py 수동 검색 — 제외어 필터 추가

기존 `pipeline.run_manual_search`에는 `excluded_scope_reason()` 호출이 없어 정기 스케줄러와 동작이 달랐다. 운영자가 수동 검색으로 동작을 확인할 때 "제외어 정책이 안 먹힌다"고 오판할 수 있는 구조.

추가된 위치: 채용 필터 직후, 마감 필터 전 (스케줄러와 동일한 순서):
```python
scope_reason = excluded_scope_reason(candidate, settings)
if scope_reason:
    logger.info("scope filtered site=%s title=%s keyword=%s", ...)
    continue
```

함께 처리: `settings = getattr(notifier, "settings", None)` 중복 할당을 루프 밖으로 이동.

---

## 변경 파일

```
app/services/broad_search_terms.py      #1 정확일치, #6 죽은 함수 삭제
app/services/business_scope_filter.py   #4 제목 한정, 미사용 import 제거
app/services/scheduler.py               #A DB 기반 2차 게이트
app/services/pipeline.py               #B 제외어 필터 추가, #C settings 중복 제거
.env.override, .env.example             #2 AI+ 검색어 3개 제거
tests/test_broad_search_terms.py        토큰/supporting 테스트 수정·추가
tests/test_business_scope_filter.py     제목 한정 테스트 추가
tests/test_scheduler_policy.py          DB 게이트 테스트 추가 (#D)
tests/test_pipeline_scope_filter.py     pipeline 제외어 테스트 신규 (#D)
docs/keyword-filter-policy-2026-06-08.md #8 문서 갱신
(삭제) appservices/ apprepositories/ appweb_static/
```

---

## 검증 결과

### 단위 테스트 (1차+2차 합산)
```bash
.venv/bin/python -m unittest \
    tests.test_broad_search_terms \
    tests.test_business_scope_filter \
    tests.test_scheduler_policy \
    tests.test_pipeline_scope_filter
# → 26건 전부 통과
```
전체 스위트의 사전 실패 2건(`test_attachments`의 `fastapi` 미설치, `test_scheduler_regression`의 stale mock)은 **본 변경과 무관**.

### 실 DB end-to-end (1차 수정 기준)
| 검색어 | 공고 | 결과 |
|---|---|---|
| `양식` | 육상양식장 통합관리 시스템 | ✅ 통과 (`matched_broad_compound_with_supporting_keyword`) |
| `통합관리` | 양식장 통합관리 시스템 설계 | ✅ 통과 |
| `유지보수` | 해양기상정보 감시 시스템 유지보수 용역 | ✅ 통과 |
| `유지관리` | 스마트항공예보시스템 유지관리 | ✅ 통과 |
| `양식` | 도로 양식 잔디 일반관리 (무관) | ✅ 차단 (회귀 없음) |

### 배포 (2026-06-08, 1차·2차 통합 완료)
```bash
docker compose build biz-monitor-scheduler-g2b biz-monitor-scheduler biz-monitor-slack
docker compose up -d biz-monitor-scheduler-g2b biz-monitor-scheduler biz-monitor-slack
```
- 3개 컨테이너 정상 기동 (에러 없음)
- 컨테이너 내 코드 반영 확인:
  - `is_broad_compound_token('육상양식')=False`, `('양식')=True` ✓
  - `scheduler.process_job`에서 `list_enabled_site_keywords(session)` 사용 ✓
  - `pipeline`에 `excluded_scope_reason` import ✓
- DB 활성 g2b 검색어 50개 확인

---

## 사후 모니터링

다음 정기 실행(09:00 / 15:00 KST) 후 확인:

- `output/logs/app.log`에서 `양식`/`통합관리` 검색 공고가 `matched_broad_compound_with_supporting_keyword`로 통과하는지
- `scope filtered` 로그로 조경·도로·폐기물류 공고가 정상 제외되는지
- 양식장 관련 공고가 Slack에 정상 공유되는지

---

*1차 수정: 2026-06-08 / 2차 수정(수정파일.md 기반): 2026-06-08*
