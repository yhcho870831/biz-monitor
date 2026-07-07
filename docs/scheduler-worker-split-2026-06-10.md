# 공고검색 스케줄러/워커 분리 + AI 추천 후행 분리 (2026-06-10)

## 배경 (왜)

운영에서 `run-scheduled-once`(또는 스케줄러 루프)가 한 프로세스에서 **수집 → 필터 →
Slack 본문 전송 → AI 추천**을 전부 순차로 처리했다. 두 가지가 겹치며 9시 배치가
15시까지 끝나지 않는 문제가 발생했다.

1. **한 사이트가 전체를 막음** — `claim_next_pending_job`이 `run_after, id` 순으로
   단일 순차 소비라, g2b처럼 느린 사이트가 뒤 사이트 전송까지 막았다.
2. **AI 추천이 임계 경로에 있었음** — `process_job` 안에서 공고 1건마다
   `evaluate_notice_relevance`를 동기 호출했다. `AI_RELEVANCE_TIMEOUT_SECONDS=300`,
   `AI_RELEVANCE_MAX_PER_RUN=10` 이면 사이트당 최대 ~50분 동안 본문 전송이 묶였다.

## 변경 내용 (무엇)

### 1. 스케줄러 = enqueue 전용
- `run-scheduled-once` / `run-scheduler-loop` 는 더 이상 직접 처리하지 않고
  `enqueue_scheduled_cycle()`(정리 + 잡 생성 + 상태 요약 로그)만 수행한다.
- `app/services/scheduler.py: enqueue_scheduled_cycle`, `app/main.py:
  command_run_scheduled`, `command_run_scheduler_loop`.

### 2. 사이트별 상시 워커
- 신규 명령 `run-worker-loop` (`command_run_worker_loop`)는 `SCHEDULER_SITE_CODES`
  스코프의 pending 잡을 계속 claim → 처리 → 사이트 완료 즉시 Slack 전송한다.
- docker-compose: `biz-monitor-worker-g2b`(g2b 전용) +
  `biz-monitor-worker-core`(d2b,iris,kimst,kmiti,nia). g2b는 항상 분리.
- `docker/run-worker.sh` 추가.

### 3. AI 추천 후행 분리 (가장 중요한 수정)
- `process_job`에서 인라인 AI 호출 제거. 본문 공유 판정은 **규칙 기반만** 사용한다.
- AI 평가 대상 공고는 `stats.site_ai_candidates`에 모아두고, **모든 사이트 본문이
  전송된 뒤** `_run_site_ai_trailing()`이 사이트별로 평가 후 별도 "AI 추천 결과"
  메시지를 보낸다.
- AI 실패·timeout은 `evaluate_notice_relevance`가 내부에서 잡아 `failed`로 기록하므로
  본문 전송에는 영향이 없다. "AI 미완료"만 남고 배치는 막히지 않는다.

### 3-1. stale job heartbeat + ownership (2026-06-10 보강)
- `jobs` 테이블에 `worker_id`, `heartbeat_at` 컬럼 추가 (additive migration:
  `app/bootstrap.py:_upgrade_schema`).
- `claim_next_pending_job(..., worker_id=...)`가 claim 시 소유 워커와 heartbeat를
  기록한다.
- 각 워커는 `run_pending_jobs` 동안 백그라운드 `_HeartbeatThread`를 띄워
  `WORKER_HEARTBEAT_INTERVAL_SECONDS`(기본 30초)마다 자기 running job의
  `heartbeat_at`을 갱신한다. 느린 g2b 수집이 메인 스레드를 막고 있어도 heartbeat는
  계속 찍힌다.
- `requeue_stale_running_jobs`는 이제 `started_at`이 아니라
  `COALESCE(heartbeat_at, started_at) < stale_before` 기준으로 재큐잉하고,
  재큐잉 시 `worker_id`/`heartbeat_at`을 비운다. → **살아있는 장기 작업은 절대
  재큐잉되지 않고, 죽은 워커가 남긴 running job만 회수**된다.
- 동시 쓰기(heartbeat 스레드 + 워커 + 스케줄러) 대비 SQLite `busy_timeout=5000`
  pragma 추가 (`app/db.py`).
- 회귀 테스트: `tests/test_jobs_heartbeat.py` (fresh heartbeat 미재큐 / cold
  heartbeat 재큐+소유권 해제 / heartbeat가 장기작업 보존 / 타 워커 job 미갱신).

> 한계/후속: 현재는 사이트 scope당 워커 1개라 split-brain 위험이 거의 없다.
> 수평 확장(같은 scope 다중 워커) 시에는 전송 직전 소유권 재확인까지 추가하는 것이
> 정석이다. 또한 AI는 본문 임계 경로에서는 빠졌지만 아직 worker scope 배치 종료 후
> 트레일링으로 돌아 다음 claim을 AI 시간만큼 늦출 수 있다 — 완전 분리는 AI 전용
> 큐/워커(`job_type='ai'`)가 다음 후보다.

### 4. 관측/안정화
- 9시/15시 enqueue 직후 `_log_job_status_summary`로
  pending/running/failed/success/skipped/retry 스냅샷을 남긴다
  (`summarize_job_counts`).
- stale `running` 자동 pending 복귀(`requeue_stale_running_jobs`,
  `JOB_RUNNING_STALE_MINUTES`)와 재시도 제한(`MAX_RETRY_COUNT`)은 기존대로 매 워커
  패스 시작 시 적용.

## 동작 변화 요약

| 항목 | 이전 | 이후 |
| --- | --- | --- |
| 스케줄러 책임 | enqueue + 처리 + 전송 + AI | enqueue + 정리 + 상태 로그 |
| 처리 주체 | 스케줄러 단일/그룹 루프 | g2b 워커 + core 워커 (상시) |
| AI 추천 | 본문 전송 전 인라인 | 본문 전송 후 사이트별 후행 메시지 |
| 본문 스레드 AI 줄 | 있었음 | 없음 (별도 "AI 추천 결과" 메시지로 이동) |
| AI timeout 영향 | 본문 전송 지연/차단 | 본문 전송 무관, "AI 미완료"만 기록 |

> 참고: 규칙은 통과 못 했지만 AI로만 추천되던 공고는 이제 본문 표에는 안 들어가고,
> "AI 추천 결과" 메시지에 별도로 표시된다.

## 배포 절차 (docker-compose 기준)

```bash
cd /home/koast/biz-monitor
docker compose build
docker compose up -d --force-recreate \
  biz-monitor-web biz-monitor-slack \
  biz-monitor-scheduler biz-monitor-worker-g2b biz-monitor-worker-core
docker rm -f biz-monitor-scheduler-g2b   # 구 컨테이너 1회 정리
```

## 검증 순서 (플랜 5단계)

1. 개발/스테이징에서 강제 enqueue:
   `docker compose exec biz-monitor-scheduler python -m app.main enqueue-scheduled-jobs`
2. 워커 로그로 pending 감소 / running 잔류 확인:
   `docker compose logs -f biz-monitor-worker-g2b biz-monitor-worker-core`
3. 전송 분리 확인 — g2b 8건 단위 조기 전송, 타 사이트 완료 즉시 전송,
   "AI 추천 결과"가 본문 뒤에 별도로 찍히는지.
4. 운영 반영 후 실제 09:00 1회, 15:00 1회 모니터링.

### 합격 기준
- 배치 종료 후 `pending=0`, `running=0`, stale job `0`
- 공고 본문 전송 성공
- AI 실패가 있어도 본문 전송은 유지 (AI는 "AI 미완료"로만 남음)

## 표준 테스트 커맨드 (검증 경로)

운영 서버의 시스템 python에는 `sqlalchemy`/`bs4`가 없으므로 **반드시 프로젝트
`.venv` 또는 컨테이너 안에서** 돌린다.

```bash
# 1) 로컬 .venv 기준 (권장)
cd /home/koast/biz-monitor
.venv/bin/python -m unittest discover -s tests -p "test_*.py"

# 2) 컨테이너 내부에서 (운영 이미지와 동일 의존성)
docker compose exec biz-monitor-worker-core \
  python -m unittest discover -s tests -p "test_*.py"

# 3) 이번 변경 핵심만 빠르게
.venv/bin/python -m unittest \
  tests.test_scheduler_regression tests.test_jobs_heartbeat
```

> 참고: 전체 62건 중 2건은 이번 변경과 무관한 사전 실패라 제외된다 → **나머지 60건 통과**.
> - `test_attachments`: `httpx`(fastapi `TestClient` 의존, 런타임엔 불필요)가 로컬·컨테이너
>   모두 없어 import 단계에서 실패. 돌리려면 `.venv/bin/pip install httpx` (또는 이미지에
>   추가). 런타임 동작과는 무관.
> - `test_pipeline_scope_filter`: 기존 키워드 필터 이슈(`docs/수정파일.md`)로 사전 실패.
>
> 이번 구조 변경 검증의 핵심은 `tests.test_scheduler_regression`,
> `tests.test_jobs_heartbeat`, `tests.test_scheduler_policy`이며 전부 통과한다.
