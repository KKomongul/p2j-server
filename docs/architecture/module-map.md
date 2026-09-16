# 모듈 구성

## 현재 존재하는 것

```
app/
├─ main.py                     앱 팩토리, 예외 핸들러 4종, X-Request-Id 미들웨어, CORS, /v1 라우터
├─ core/
│  ├─ config.py                Settings (.env, 운영에서 JWT_SECRET 기본값 거부)
│  ├─ errors.py                AppError · ERROR_CATALOG(35종) · Pydantic 오류 → 한국어
│  ├─ response.py              ok() · paged() · no_content() · encode/decode_cursor()
│  ├─ security.py              bcrypt · access JWT(30분) · refresh 원문/해시 생성
│  ├─ deps.py                  get_db · get_current_user (CurrentUser)
│  ├─ scheduler.py             매일 04:10 KST 배치 태스크 (asyncio, 의존성 없음)
│  └─ time.py                  service_today() 04:00 KST · to_kst_iso() · week_range() · as_utc()
├─ db/
│  ├─ base.py                  Base · naming_convention · BigIntPK · TimestampMixin
│  ├─ session.py               async engine · sessionmaker (테스트에서 교체 가능)
│  ├─ redis.py                 redis.asyncio 클라이언트
│  └─ models/                  User · RefreshToken · Goal · Todo
│                              UserDailyStat · LoadCheck
│                              Group · GroupMember · GroupInvite
│                              Declaration · DeclarationItem · Proof · Comment · Reaction
├─ schemas/
│  ├─ user.py                  user_to_dict() · public_user_to_dict()
│  ├─ auth.py                  Signup/Login/Refresh/Logout 요청
│  ├─ goal.py                  Goal 요청 · goal_to_dict()
│  ├─ todo.py                  Todo 요청(bulk 포함) · todo_to_dict() · summary_dict()
│  ├─ ai.py                    ParseRequest
│  ├─ load_check.py            LoadCheckResponseRequest
│  └─ social.py                Group/Join/Declaration/Proof/Presign/Comment/Reaction 요청
├─ api/v1/
│  ├─ router.py
│  └─ endpoints/
│     ├─ health.py             GET /health (probe 는 Depends 로 교체 가능)
│     ├─ auth.py               signup · login · refresh · logout · me
│     ├─ goals.py              목록(커서) · 생성 · 상세 · 수정 · archive · 삭제
│     ├─ todos.py              목록 · week · CRUD · bulk · complete · uncomplete · postpone
│     ├─ ai.py                 parse · quota · load-check · load-check 응답
│     ├─ stats.py              summary · goals/{id}
│     ├─ groups.py             그룹 CRUD · 초대 · 선언 · 인증샷 · 피드 · 랭킹 · 스트릭
│     ├─ posts.py              게시물 · 댓글 · 리액션
│     └─ uploads.py            presign
└─ services/
   ├─ auth.py                  signup · login · issue_session · rotate_refresh_token · revoke_*
   ├─ goals.py                 goal_progress · 팔레트 · end_date 계산 · 커서 목록
   ├─ todos.py                 TODO 규칙 (선언 잠금 · 날짜 범위 · 미루기 · bulk 롤백)
   ├─ ai_parse.py              /ai/parse 조립 (목표 힌트 · 쿼터 · 파이프라인)
   ├─ ai/                      schemas · llm (GPT-4o-mini 구조화 출력) · rules · pipeline · quota
   ├─ stats.py                 recalc_day (집계 즉시 반영) · 연속 기록 · summary · goal_stats
   ├─ load_check.py            계획량 안내 판정 (집계 기반, LLM 미사용) · 하루 1회 캐시
   ├─ groups.py                정원·초대코드·탈퇴·구성원 현황
   ├─ declarations.py          선언 생성(즉시 잠금) · 그날 현황 · 항목 상태 판정
   ├─ feed.py                  인증샷 · 사용자별 피드(미달성 포함) · 커서
   ├─ posts.py                 댓글 · 리액션(1인 1개 UPSERT)
   ├─ ranking.py               기간별 순위 · 그룹 스트릭 판정
   ├─ uploads.py               presign (자격증명 없으면 503 STORAGE_UNAVAILABLE)
   └─ batch.py                 04:10 잡 3종 (집계 마감 · 그룹 스트릭 · 토큰 정리)

app/jobs.py                    배치 수동 실행 (`python -m app.jobs [--date YYYY-MM-DD]`)
```

## 앞으로 만들 것

**빈 폴더를 미리 만들지 않는다.** 실제 구현을 시작하는 PR 에서 그 파일만 만든다.
지금 껍데기를 만들어 두면 "있는데 동작하지 않는" 상태가 길어지고, 모바일이 구현 여부를 착각한다.

```
app/api/v1/endpoints/          app/services/              app/db/models/
├─ users.py                    ├─ users.py
├─ todos.py (+reorder P1)                                 ├─ ai_parse.py (정확도 로그)
├─ ai.py (+transcribe P1)      ├─ ai/ (Colab 검증 코드로 rules.py 교체)
└─ devices.py (P1)                                        └─ device.py (P1)
```

`uploads.py` 의 presign 은 골격만 있고 **Firebase 자격증명이 없으면 503** 이다.
`FIREBASE_CREDENTIALS_PATH` · `FIREBASE_STORAGE_BUCKET` 을 채우면 그때 살아난다.
인증샷 기능 자체는 `file_key` 만 받으므로 그 전에도 끝까지 동작한다.

모듈 이름은 `02-api-v1.md` 의 섹션 구분을 그대로 따른다.
모바일의 feature 폴더(`auth` / `todo` / `goal`)와도 이름이 맞아, 회의할 때 서로 헷갈리지 않는다.

## 구현할 때 지켜야 할 것

- **엔드포인트 = 세 줄.** 요청 스키마 검증 → `services.*` 호출 → `ok()` / `paged()` / `no_content()`.
- **목록 응답** — 서비스가 `(items, next_cursor)` 를 돌려주면 `paged(items, next_cursor)`.
  커서는 `encode_cursor({"id": ..., "at": ...})`. 클라이언트는 해석하지 않는다.
- **인증** — 새 엔드포인트는 `user: CurrentUser` 인자를 받는 것이 기본이다. 인증 없는 경로는
  `/auth/signup`, `/auth/login`, `/auth/refresh`, `/health` 네 개뿐.
- **직렬화** — 응답 dict 를 만드는 함수를 `app/schemas/<도메인>.py` 에 둔다 (`user_to_dict` 처럼).
  ORM 객체를 그대로 반환하지 않는다. `response_model` 을 쓰지 않는다.
- **날짜** — `YYYY-MM-DD`, 시각은 `to_kst_iso()`. 오늘은 `service_today()`.
- **204** — `no_content()`. 감싸지 않는다. 모바일 `uncomplete` 가 본문 없음을 전제한다.
- **오류** — `AppError(code)` 또는 `errors.py` 의 이름 있는 클래스. 소유권 위반은 404 (존재를 숨김), 그룹 권한은 403.
- **N+1** — 목록 + 조인은 `selectinload` 또는 명시적 join. 5주차 전에 "TODO 목록 + 목표 제목" 쿼리를 한 번 끝까지 짜 본다.
