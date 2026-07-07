# [핸드오프] KMITI 마감일 미파싱 / 작성일 오라벨 수정 (A+C) - 2026-06-10

## 0. 한 줄 요약
KMITI(기상산업기술원) 공고에서
- 마감 지난 공고가 Slack에 공유되고,
- Slack의 `입찰마감`에 게시일(작성일)이 잘못 표시되던 버그를 수정했다.

원인은
1. KMITI 수집기가 제목 속 마감일을 `deadline_at`에 채우지 못했고,
2. 표시 로직이 `period_text`의 작성일을 마감일처럼 사용했기 때문이다.

현재 상태는 **A(제목에서 마감 파싱) + C(작성일을 마감으로 쓰지 않기) + 회귀 테스트 + 운영 반영 완료**다.

## 1. 근본 원인

실제 사례 - `notice id=9653`
- 제목: `「2026년도 기상R&D 아이디어 공모전」(~4.24.(금) 18:00)`
- 저장값: `deadline_at=None, open_at=None, start_at=None`
- `period_text='작성일 2026.04.01'`, `raw_payload.posted_at='2026.04.01'`
- Slack 표시: `입찰마감: 2026-04-01 00:00`

즉 두 결함이 겹쳐 있었다.

1. 필터 우회
- `app/services/deadline.py:is_active_notice()`는 일반 사이트에서 사실상 `deadline_at`만 본다.
- KMITI 수집기가 `deadline_at`을 비워두면 지난 공고도 활성으로 남는다.

2. 작성일 오라벨
- `app/services/notifier.py:_display_deadline()` / `_sort_candidate_key()`가
  `deadline_at`이 없을 때 `extract_datetimes(period_text)[-1]`를 fallback으로 사용했다.
- `period_text='작성일 2026.04.01'`이면 작성일이 그대로 마감일처럼 표시됐다.

보조 원인
- `app/utils.py:extract_datetimes()`는 `YYYY.MM.DD` 형태만 인식하므로
  제목의 `(~4.24.(금) 18:00)`은 못 잡고, 작성일 `2026.04.01`은 잡았다.

## 2. 작업 범위
- **A** 제목 `~M.D.(요일) HH:MM` 패턴 파서 추가 및 KMITI 수집기에 연결
- **C** 작성일/등록일/게시일을 마감 fallback에서 제외
- 단위 테스트 추가
- 운영 서버 배포 및 컨테이너 재기동

## 3. 최종 상태

### A. 제목 마감 파싱 - 완료
적용 파일
- `app/utils.py`
- `app/collectors/kmiti.py`

반영 내용
- `parse_title_deadline(title, reference=None)` 추가
- 연도 없으면 게시일 기준 연도 사용
- 12월 게시 -> 1월 마감처럼 명확한 wrap일 때만 +1년
- 시간 없으면 `23:59` 보정
- KMITI 수집기에서 `posted_at`을 기준 연도로 써서 `deadline_at` 채움

확인한 예시
- `(~4.24.(금) 18:00)` + ref `2026-04-01` -> `2026-04-24 18:00`
- `(~5/29)` + ref `2026-05-19` -> `2026-05-29 23:59`
- `(~1.10. 18:00)` + ref `2025-12-20` -> `2026-01-10 18:00`
- 패턴 없음 -> `None`

### C. 작성일 오라벨 방지 - 완료
적용 파일
- `app/services/notifier.py`

반영 내용
- `_POSTING_DATE_MARKERS = ("작성일", "등록일", "게시일")` 추가
- `_period_text_deadline(candidate)` 헬퍼 추가
- `_sort_candidate_key()`와 `_display_deadline()`가
  `deadline_at -> _period_text_deadline() -> open_at -> start_at`
  순서로만 deadline fallback을 계산하도록 변경

효과
- `period_text='작성일 2026.04.01'`인 경우 `입찰마감`은 더 이상 `2026-04-01 00:00`으로 표시되지 않음
- 실제 기간 범위(`2026.06.01 ~ 2026.06.15`)는 계속 정상적으로 마지막 날짜를 fallback으로 사용

### 테스트 - 완료
추가 파일
- `tests/test_kmiti_deadline.py`

검증 항목
- 제목 마감 파싱
- 연말/연초 year wrap
- 슬래시 날짜의 `23:59` 보정
- 만료 공고 필터링
- 작성일 오라벨 방지
- 실제 기간 범위 fallback 유지

추가로 같은 롤아웃에서 통과 확인한 회귀
- `tests.test_scheduler_regression`
- `tests.test_business_scope_filter`

### 운영 반영 - 완료
반영 방식
- 서버: `/home/koast/biz-monitor`
- 컨테이너 재빌드/재기동 완료

## 4. 실제 수정 요약

### `app/utils.py`
- `parse_title_deadline()` 추가
- `_TITLE_DEADLINE_RE` 추가

### `app/collectors/kmiti.py`
- `parse_title_deadline` import
- 제목에서 마감일 추출 후 `deadline_at`에 저장

### `app/services/notifier.py`
- `_POSTING_DATE_MARKERS` 추가
- `_period_text_deadline()` 추가
- `_sort_candidate_key()` 수정
- `_display_deadline()` 수정

### `tests/test_kmiti_deadline.py`
- 신규 회귀 테스트 추가

## 5. 검증 커맨드

```bash
cd /home/koast/biz-monitor
.venv/bin/python -m py_compile app/utils.py app/collectors/kmiti.py app/services/notifier.py
.venv/bin/python -m unittest tests.test_kmiti_deadline -v
.venv/bin/python -m unittest tests.test_scheduler_regression -v
.venv/bin/python -m unittest tests.test_business_scope_filter -v
```

실행 결과
- `tests.test_kmiti_deadline`: 9 tests OK
- `tests.test_scheduler_regression`: 3 tests OK
- `tests.test_business_scope_filter`: 18 tests OK

## 6. 배포 메모

현재 워커 구성 기준 재배포 명령은 아래가 맞다.

```bash
cd /home/koast/biz-monitor
docker compose up -d --build --remove-orphans
```

현재 운영 컨테이너
- `biz-monitor-scheduler`
- `biz-monitor-slack`
- `biz-monitor-web`
- `biz-monitor-worker-g2b`
- `biz-monitor-worker-d2b`
- `biz-monitor-worker-research`
- `biz-monitor-worker-light`

주의
- 이미 예전에 공유된 잘못된 Slack 메시지는 수정되지 않는다.
- 이후 신규 수집분 / 재수집분부터 정상 표기된다.
- 과거 notice row도 동일 공고가 다시 수집되어 `upsert_notice`를 타야 `deadline_at`이 갱신된다.

## 7. 관련 후속 작업(같은 롤아웃)

이번 KMITI 마감일 수정과 함께 같은 날 아래 운영 개선도 반영했다.

1. 워커 분리 강화
- 기존: `g2b` + `worker-core`
- 현재:
  - `g2b`
  - `d2b`
  - `iris,kmiti`
  - `kimst,nia`

2. 오탐 회귀 테스트 추가
- `물류 운반`
- `수경시설 운영`
- `유지보수` 단독 검색은 추가 도메인 키워드 없으면 공유 금지
- `유지보수 + 해양관측` 같은 조합형은 공유 허용

이 항목들은 KMITI 버그 자체와 직접 원인은 다르지만, 같은 배포 묶음으로 운영 반영됐다.

## 8. 남은/후속

1. NIA 마감 파싱
- NIA는 본문에 기간이 있어도 수집기가 못 잡는 경우가 있어 별도 보강이 필요하다.

2. 마감 미상 공고 정책
- `deadline_at=None`이고 작성일 fallback도 없는 공고는 현재 `미기재`로 남는다.
- 이런 공고를 공유할지 말지는 별도 정책 결정이 필요하다.

3. 과거 KMITI 데이터 보정
- 필요한 경우 특정 기간 KMITI 공고를 재수집하거나 backfill 스크립트로 `deadline_at`을 보정할 수 있다.

## 9. 관련 파일
- `app/utils.py`
- `app/collectors/kmiti.py`
- `app/services/notifier.py`
- `app/services/deadline.py`
- `tests/test_kmiti_deadline.py`
- `tests/test_scheduler_regression.py`
- `tests/test_business_scope_filter.py`
