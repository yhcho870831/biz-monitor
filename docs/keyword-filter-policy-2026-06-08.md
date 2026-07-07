# biz-monitor 검색어·제외어 정책 변경 (2026-06-08)

공공 사업공고 Slack 모니터링(`biz-monitor`)에서 **무관한 공고 노이즈**를 줄이기 위해 적용한 변경 사항을 정리한 문서입니다.

---

## 대상 시스템

| 항목 | 내용 |
|---|---|
| 프로젝트 경로 | `/home/koast/biz-monitor` |
| Docker Compose | `docker-compose.yml` |
| 주요 컨테이너 | `biz-monitor-scheduler-g2b`, `biz-monitor-scheduler`, `biz-monitor-slack` |
| 운영 설정 | `.env` + `.env.override` |
| 검색어 DB | SQLite `data/app.db` → `site_keywords` 테이블 |

---

## 변경 배경

- g2b(나라장터)에 `기상`, `AI`, `데이터`, `GIS` 등 **광역 단일 검색어**가 많아 검색 800건 이상·무관 공고 다수 Slack 공유
- 공고 **제외어**가 코드에 하드코딩되어 운영 중 수정이 어려움
- `유지관리` / `유지보수` / `통합관리` / `양식`은 회사 사업과 관련 있으나, 단독으로 쓰면 노이즈가 큼

---

## 처리 파이프라인 (변경 후)

```text
[사이트별 검색어] → 포털 검색
    → 채용 공고 필터
    → 공고 제외어 필터 (EXCLUDED_SCOPE_KEYWORDS, env 설정)
    → 마감 필터
    → 룰 기반 적합성 (회사 프로젝트 키워드)
    → 광역 단독 검색어 추가 검증 (양식/유지보수/유지관리/통합관리)
    → AI 적합성 (선택, 임계값 70)
    → g2b 최소 별점 1
    → Slack 전송 (사이트당 상한)
```

---

## 1. g2b 검색어 — 조합형 우선 (50개)

설정: `.env.override` → `G2B_KEYWORDS`  
DB 동기화: `app/repositories/sites.py` → `sync_site_keywords()`

### 1-1. 비활성화한 광역 단일어 (DB `enabled=0`)

나라장터 정기 검색에서 **더 이상 사용하지 않음**:

`기상`, `기후`, `해양`, `수산`, `양식`(단독 DB row는 재활성 — 아래 광역 정책 참고), `AI`, `인공지능`, `데이터`, `빅데이터`, `모니터링`, `자동화`, `GIS`, `공간정보`, `유지보수`(구 row), `통합관리`(구 row)

### 1-2. 단독 광역 검색어 (4개)

검색은 수행하되, 수집된 공고는 **제목/본문에 다른 도메인 키워드가 함께 있을 때만** 통과:

| 검색어 | 예시 통과 공고 |
|---|---|
| `양식` | `육상양식장 통합관리 시스템` (`육상양식` 동반) |
| `유지보수` | `해양기상정보 감시 시스템 유지보수 용역` |
| `통합관리` | `양식장 통합관리 시스템 설계` |
| `유지관리` | `스마트항공예보시스템 유지관리` |

### 1-3. 포털 복합 검색어 (9개)

나라장터에 **두 단어 이상**으로 검색:

- `기상 유지보수`, `해양기상 유지보수`, `항공기상 유지보수`, `스마트항공예보 유지보수`
- `기상 통합관리`, `해양 통합관리`, `양식장 통합관리`
- `해양 양식`, `수산 양식`

> ℹ️ **실효성 참고 (2026-06-08 라이브 검증)**: g2b 공고명은 공백을 AND가 아닌 **리터럴 substring**으로 검색하므로 이 복합어들은 대부분 0건이다(`해양기상 유지보수`·`수산 양식`·`기상 통합관리`=0건, `양식장 통합관리`=1건). 의도는 광역 단독어(`유지보수`/`통합관리`/`양식`)+도메인 키워드 동반 검증으로 이미 커버되나, 리터럴 일치 공고를 놓치지 않기 위한 안전망으로 **유지**한다.

### 1-4. 도메인 특화 검색어 (37개)

**기상·예보 (18)**  
기상정보, 기상자료, 기상관측, 기상센서, 수치예보, 수치예보모델, 수치예보시스템, 장기예보, 확률장기예보, 항공기상, 항공기상예보, 스마트항공예보, 해양기상, 도로기상, 상세기상, 기상관측부이, 기상레이더, 기상재해

**수산·양식·해양 (12)**  
스마트양식, 육상양식, 육상양식장, 양식장 자동화, 아쿠아포닉스, RAS, 어선, 어업, 해양안전, 선박, 해양환경, 해양관측

**장비·분석 (2)**  
데이터 로거, 영상분석

**발주기관 (5)**  
국립수산과학원, 국립해양조사원, 해양환경공단, 기상청, 항공기상청

> ~~AI 조합 (AI + 기상/해양/양식)~~ 은 g2b 공고명 리터럴 검색에서 `" + "` 포함 제목이 없어 항상 0건이라 **제거**(2026-06-08). 기상/해양/양식 도메인 키워드로 커버됨.

### 1-5. 다른 사이트

kimst, nia, d2b, kmiti, iris 검색어는 **이번 변경에서 수정하지 않음** (`.env.override` 기존 값 유지).

---

## 2. 공고 제외어 — env 설정 (25개)

설정: `.env.override` → `EXCLUDED_SCOPE_KEYWORDS`  
코드 기본값: `app/services/business_scope_filter.py` → `DEFAULT_EXCLUDED_SCOPE_KEYWORDS`

공고 **제목**에 아래 단어가 포함되면 Slack 공유 대상에서 제외:

> 매칭 범위는 **제목 한정**입니다(2026-06-08 변경). 이전에는 본문(raw_payload) 전체를 substring 검사해 `공원`·`차량`·`이온` 같은 짧은 단어가 본문 어디든 있으면 공고 전체가 과잉 제외되는 문제가 있었습니다.

| 카테고리 | 단어 |
|---|---|
| 조경·시설 | 조경, 수목, 공원, 잔디, 수경시설, 수경시설 운영, 물놀이형 수경시설 |
| 건설·토목 | 도로포장, 도로 포장, 건축, 상수도, 하수관로, 관망정비 |
| 물류·폐기물 | 폐기물, 물류반송, 반송설비, 물류 운반, 물류운반 |
| 채용·교육 | 교육생, 인턴, 채용 |
| 기타 | 차량, 이온, 재난대비, 재난 대비, 청소 |

### 제외하지 않는 단어 (회사 사업 관련)

`유지관리`, `유지보수`, `통합관리`, `양식`

> `잔디 유지관리`처럼 **잔디**와 함께 있는 공고는 `잔디` 때문에 여전히 제외됩니다.

### 운영 방법

제외어 추가/삭제 시 코드 수정 없이 `.env.override`만 변경 후 스케줄러 컨테이너 재시작:

```bash
cd /home/koast/biz-monitor
docker compose up -d biz-monitor-scheduler-g2b biz-monitor-scheduler
```

---

## 3. 광역 단독 검색어 로직

신규 모듈: `app/services/broad_search_terms.py`

| 함수 | 역할 |
|---|---|
| `BROAD_COMPOUND_ONLY_TERMS` | `유지보수`, `유지관리`, `통합관리`, `양식` |
| `is_exact_broad_compound_search_term()` | 검색어가 위 4개 중 하나인지 판별 |
| `is_broad_compound_token()` | 키워드가 광역어 **정확일치**인지 판별 |
| `filter_supporting_keyword_matches()` | 프로젝트 키워드 매칭 시 광역어 제외 |

연동 파일:

- `app/services/relevance.py` — 단독 광역 검색 시 supporting 키워드 필요
- `app/services/scheduler.py` — 통과 후 사이트 키워드 목록과 공고 본문 재검증

> ⚠️ **버그픽스 (2026-06-08)**: `is_broad_compound_token()`이 부분문자열 매칭이라 `양식`이 `육상양식`·`스마트양식`·`양식장 통합관리` 등 모든 양식 도메인 키워드의 부분문자열이 되어, supporting 키워드가 전멸 → 1-2절의 양식/통합관리 "통과 예시"가 실제로는 차단됐음. **정확일치 매칭으로 수정**하여 `양식`(단독)만 제외하고 `육상양식` 등은 supporting으로 보존.

스케줄에서 검색어 skip 하던 `EXCLUDED_SCHEDULED_SITE_KEYWORDS`(g2b 유지보수/통합관리)는 **제거** (빈 dict).

---

## 4. g2b Slack 공유 정책

`app/services/scheduler.py` → `_min_priority_for_site("g2b")` = **1**

- g2b는 **별 1개 이상** 공고만 Slack 공유 (README 정책과 일치)
- AI 적합성 70점 이상이면 예외적으로 공유 가능 (기존 정책)

Slack 상한 (`.env.override`):

- `SLACK_MAX_NOTICES_PER_SITE=10`
- `SLACK_MAX_NOTICES_G2B=10`
- `SLACK_MAX_ZERO_STAR_PER_SITE=10`

---

## 5. 변경된 파일 목록

| 파일 | 변경 요약 |
|---|---|
| `.env.override` | `G2B_KEYWORDS`, `EXCLUDED_SCOPE_KEYWORDS` |
| `.env.example` | 동일 항목 예시 갱신 |
| `app/config.py` | `excluded_scope_keywords` 설정 필드 추가 |
| `app/services/business_scope_filter.py` | env 기반 제외어, 기본값 정리 |
| `app/services/broad_search_terms.py` | **신규** — 광역 단독 검색 정책 |
| `app/services/relevance.py` | 광역 단독 검색 적합성 로직 |
| `app/services/scheduler.py` | 제외어 settings 전달, g2b min priority, 광역 검증 |
| `app/repositories/sites.py` | `sync_site_keywords()` DB 동기화 함수 |
| `tests/test_business_scope_filter.py` | 제외어 테스트 |
| `tests/test_broad_search_terms.py` | **신규** — 광역 검색어 테스트 |

---

## 6. DB·배포 작업

```bash
# 검색어 DB 동기화 (호스트에서)
cd /home/koast/biz-monitor
DATABASE_URL="sqlite:////home/koast/biz-monitor/data/app.db" .venv/bin/python -c "
from app.config import load_settings
from app.db import create_db_engine, create_session_factory
from app.repositories.sites import sync_site_keywords
s = load_settings()
engine = create_db_engine('sqlite:////home/koast/biz-monitor/data/app.db')
Session = create_session_factory(engine)
with Session() as session:
    print(sync_site_keywords(session, 'g2b', s.site_keywords['g2b']))
"

# 이미지 빌드 및 재시작
docker compose build biz-monitor-scheduler-g2b biz-monitor-scheduler biz-monitor-slack
docker compose up -d biz-monitor-scheduler-g2b biz-monitor-scheduler biz-monitor-slack
```

> `.env`의 `G2B_KEYWORDS`만 바꿔서는 부족합니다. `site_keywords` 테이블에 예전 키워드가 남을 수 있으므로 **`sync_site_keywords` 또는 Slack `삭제` 명령**으로 DB도 맞춰야 합니다.

---

## 7. Slack 검색어 관리 (기존)

```text
@biz-monitor 공고:나라장터/키워드 추가
@biz-monitor 공고:나라장터/키워드 삭제
@biz-monitor 검색어 보여줘
```

---

## 8. 검증 체크리스트

- [ ] `docker exec biz-monitor-scheduler-g2b` 로 g2b 활성 검색어 50개 확인
- [ ] `유지보수`, `통합관리`, `양식`이 `EXCLUDED_SCOPE_KEYWORDS`에 **없는지** 확인
- [ ] 정기 실행 로그 `search_results` 건수 감소 확인 (`output/logs/app.log`)
- [ ] `.venv/bin/python -m unittest tests.test_broad_search_terms tests.test_business_scope_filter tests.test_scheduler_policy`

---

## 9. 변경 이력 요약

| 일자 | 내용 |
|---|---|
| 2026-06-08 | g2b 광역 단일 검색어 비활성, 조합형 40개 세트 적용 |
| 2026-06-08 | `EXCLUDED_SCOPE_KEYWORDS` env 설정화 |
| 2026-06-08 | g2b min priority 1 복원 |
| 2026-06-08 | `유지관리` 공고 제외 해제 + g2b 검색어 추가 |
| 2026-06-08 | `유지보수`/`통합관리`/`양식` 제외 해제, 단독·복합 검색 정책 분리 |
| 2026-06-08 | **버그픽스**: `is_broad_compound_token` 부분문자열→정확일치 (양식 도메인 차단 해소) |
| 2026-06-08 | **버그픽스**: 동작 불능 `AI + *` 검색어 3개 제거 (53→50) |
| 2026-06-08 | **버그픽스**: 제외어 매칭을 본문 전체→제목 한정으로 축소 (과잉 제외 해소) |
| 2026-07-01 | `청소` 제외어 추가 |
| 2026-06-08 | 정리: 미사용 `is_broad_compound_search_term` 함수, 빈 잔여 디렉토리 제거 |

---

*문서 작성: 2026-06-08*
