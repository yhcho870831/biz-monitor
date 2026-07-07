# biz-monitor

공공 사업공고를 수집해서 Slack으로 공유하고, 참여사업 캘린더까지 운영하는 내부 모니터링 도구입니다.

## 운영 대상 사이트

- 나라장터
- NIA
- KIMST
- IRIS
- 기상산업기술원
- D2B(국방전자조달)

## 정기 실행 정책

- 정기 검색 시간
  - 오전 9시
  - 오후 3시
- 스케줄러는 `Asia/Seoul` 기준으로 동작합니다.
- 정시에 실행되지 못한 경우 놓친 회차를 catch-up 합니다.
- 주말에는 정기 검색과 업데이트를 하지 않습니다.
- 대한민국 공휴일에도 정기 검색과 업데이트를 하지 않습니다.

관련 설정:

```env
SCHEDULER_ENABLED=true
SCHEDULER_SKIP_WEEKENDS=true
SCHEDULER_SKIP_PUBLIC_HOLIDAYS=true
SCHEDULE_TIME_1=09:00
SCHEDULE_TIME_2=15:00
```

## 현재 기본 검색어

### 나라장터

- 기상
- 양식
- 육상양식
- 육상양식장
- 국립수산과학원
- 스마트양식
- 양식장 자동화
- AI + 기상
- AI + 해양
- AI + 양식

### NIA

- 기상
- 양식
- 해양
- 데이터 바우처
- 공동활용

### KIMST

- 양식
- 양식시스템
- 아쿠아포닉스
- RAS
- 어선
- 안전

### IRIS

- 기상
- 기후
- 기상정보
- 해양
- 양식
- 스마트양식
- 아쿠아포닉스
- RAS
- AI

### 기상산업기술원

- 양식
- 기상
- 기상재해
- 기상정보
- 공고
- 구매
- 사업공고
- 용역
- 입찰
- 입찰공고
- AI

### D2B

- 기상
- 양식

## Slack 명령어

### 검색어 추가

```text
@biz-monitor 공고:{사이트명}/{검색어} 추가
```

예시:

```text
@biz-monitor 공고:KIMST/국제사회IUU 전자모니터링 시스템 추가
@biz-monitor 공고:나라장터/기상레이더 추가
@biz-monitor 공고:D2B/기상관측 추가
```

### 검색어 삭제

```text
@biz-monitor 공고:{사이트명}/{검색어} 삭제
```

### 검색어 목록 확인

```text
@biz-monitor 검색어 보여줘
```

### 중요 공고 목록 확인

```text
@biz-monitor 공고리스트
```

### 참여사업 일정표 확인

```text
@biz-monitor 일정표
```

### 도움말

```text
@biz-monitor 도움
@biz-monitor 명령어
```

## AI 적합성 판단

공고 검색은 기존처럼 사이트별 검색어로 먼저 수집합니다. 검색어에 걸리지 않은 공고는 시스템이 볼 수 없으므로, 검색어 관리는 계속 필요합니다.

수집된 공고는 기존 룰 기반 판단 이후 AI 적합성 판단을 추가로 수행할 수 있습니다.

- 기존 룰: 키워드, 수요기관/기관명, 금액, 연구용역 여부로 별점을 계산합니다.
- AI 판단: 공고 제목, 기관, 본문/첨부 요약, 회사 과거 수행사업을 함께 보고 의미 기반으로 검토 가치가 있는지 판단합니다.
- AI 판단 결과가 기준점 이상이면 기존 룰에서 놓친 공고도 Slack 공유 대상이 될 수 있습니다.
- GPT OAuth는 `biz-monitor` 컨테이너에 저장하지 않습니다. `biz-monitor`는 내부 HTTP gateway만 호출하고, 실제 모델 호출은 OpenClaw `cron-google` agent가 수행합니다.

운영 설정 예시는 다음과 같습니다.

```env
AI_RELEVANCE_ENABLED=true
AI_RELEVANCE_GATEWAY_URL=http://host.docker.internal:8091/v1/agent
AI_RELEVANCE_AGENT=cron-google
AI_RELEVANCE_SHARE_THRESHOLD=70
AI_RELEVANCE_MAX_PER_RUN=30
```

모든 명령 응답은 원본 메시지의 thread로 돌아갑니다.

## 공고 표시 정책

- 사이트별로 묶어서 Slack에 전송합니다.
- 사업명은 가능한 경우 클릭 가능한 링크로 제공합니다.
- 기본 표시 정보
  - 사업명
  - 입찰마감
  - 발주처
  - 금액
  - 중요도
  - 태그

### 중요도 규칙

아래 조건을 만족할 때 1점씩 부여합니다.

- 발주처가 회사 수주 이력 발주처와 일치 또는 유사
- 금액이 1억 원 이상
- 연구용역으로 판별됨

표시:

- `★★★`
- `★★☆`
- `★☆☆`
- `☆☆☆`

### 태그 색상

- `🟦 연구용역`
- `🟧 제작용역`
- `🟩 물품구매`
- `🟪 일반용역`
- `⬜ 기타`

### 사이트별 추가 정책

- 나라장터는 `★ 1개 이상` 공고만 공유합니다.
- IRIS `공모예고(사업일정)`은 제목 앞에 `[사전공고]`를 붙입니다.
- 채용 관련 공고는 모든 사이트에서 공통 필터링합니다.
- `인재양성`이 포함된 공고도 공통 필터링합니다.

## 0건 / 무공유 안내

정기 실행 후 아래 두 경우를 구분해서 안내합니다.

- 검색 결과 자체가 0건인 경우
  - `검색결과 0건입니다.`
- 검색 결과는 있었지만 조건에 맞는 공고가 없어 공유되지 않은 경우
  - `공유할 공고가 없습니다.`

## 첨부파일 다운로드 정책

현재 우선 적용 대상:

- 나라장터
- D2B

대상 조건:

- `★ 1개 이상` 공고

첨부 우선순위:

1. 제안요청서
2. 공고문
3. 과업지시서
4. 구매요구서

첨부파일은 내부망 다운로드 링크로 제공할 수 있으며, 공고 cleanup 시 같이 삭제됩니다.

## 요약 정보 정책

`★ 1개 이상` 공고는 첨부 또는 상세 본문을 기준으로 아래 5개 항목을 요약할 수 있습니다.

- 사업목적
- 핵심수행업무
- 요구성능
- 정량 목표
- 기간

정량 목표가 명확하지 않으면 `미확인`으로 표시합니다.

## 참여사업 캘린더

웹 화면:

- `/calendar`

기본 정책:

- 체크 시 참여사업으로 등록
- 체크 해제 시 달력 표시만 비활성화
- 재체크 시 기존 row 재활성화
- 직접등록 가능
- 과거이관 데이터 관리 가능
- 과거 3년 ~ 올해 말까지 조회
- `priority_score > 0` 공고만 캘린더 표시

## 로컬 실행

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m app.main init-db
python -m app.main show-config
python -m app.main run-slack
python -m app.main run-web --host 0.0.0.0 --port 8080
```

## 수동 검색 예시

```bash
python -m app.main manual-search --site kimst --term 아쿠아포닉스 --dry-run
python -m app.main manual-search --site nia --term 기상 --dry-run
python -m app.main manual-search --site g2b --term 기상 --dry-run
python -m app.main manual-search --site d2b --term 기상 --dry-run
```

## 정기 실행 테스트

```bash
python -m app.main run-scheduled-once
python -m app.main run-scheduler-loop
```

## 회사 프로젝트 seed

회사 과거 수행 사업은 JSON 파일로 넣습니다.

- 템플릿: [company_projects.template.json](C:\Users\87yon\codex\seeds\company_projects.template.json)

적용:

```bash
python -m app.main seed-company-projects --file seeds/company_projects.template.json
```

## Docker 운영

운영은 OpenClaw 컨테이너 내부가 아니라 같은 서버에서 별도 컨테이너로 분리합니다.

- `biz-monitor-web`
- `biz-monitor-slack`
- `biz-monitor-scheduler`

관련 파일:

- [Dockerfile](C:\Users\87yon\codex\Dockerfile)
- [docker-compose.yml](C:\Users\87yon\codex\docker-compose.yml)
- [docker/run-slack.sh](C:\Users\87yon\codex\docker\run-slack.sh)
- [docker/run-scheduler.sh](C:\Users\87yon\codex\docker\run-scheduler.sh)
- [docker/README.md](C:\Users\87yon\codex\docker\README.md)
