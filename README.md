# p2j-server

**P2J** — P들을 위한 TODO List 공유 앱의 백엔드. **Python 3.12 + FastAPI**.

말로 아무렇게나 뱉은 목표를 AI 가 할 일로 쪼개 주고, 그룹에 선언한 뒤 인증샷으로 확인하는 서비스다.
모바일 앱은 별도 저장소 [`p2j-mobile`](https://github.com/Kimtaehan2/p2j-mobile) 에 있다.

| 담당 | 이름 |
| --- | --- |
| 백엔드 | 김태한 |
| DB · AI | 박영준 |
| 프론트엔드 | 김지호 (`p2j-mobile`) |

> 2026-09-05 팀 결정으로 백엔드를 NestJS 에서 **FastAPI 로 전환**했다.
> 이유와 대응표는 [`docs/decisions/0003-fastapi.md`](docs/decisions/0003-fastapi.md).

---

## 현재 상태 (3~9주차 BE 항목 — 스캐폴딩 · 인증 · 목표 · TODO · AI 파싱)

**인증 · 목표 · TODO · AI 파싱까지 구현되어 있다.** 선언·증거·그룹·통계·04:00 배치는 아직 없다.

동작하는 것:

- FastAPI 앱 (PORT 8000, 경로 접두 `/v1`)
- 환경변수 검증 (`pydantic-settings`, `.env`)
- 공통 응답 래퍼 `{ data }` / `{ data, page }` 와 커서 인코딩
- **공통 오류 핸들러** `{ error: { code, message, details } }` — FastAPI 기본 `{"detail": [...]}` 를 덮어쓴다.
  검증 오류는 **400** + 필드별 **한국어** 메시지. 404·405·500 도 같은 형식
- 오류 코드 카탈로그 35종 (`app/core/errors.py`) — API 명세 §1.4 와 1:1
- **인증 API** `/v1/auth/signup · login · refresh · logout · me` — bcrypt, access 30분, refresh rotation·재사용 감지 (§3)
- **목표 API** `/v1/goals` 목록(status 필터·커서) · 생성 · 상세 · 수정 · `archive` · 삭제 (§4). `progress` 는 요청 시 계산
- **TODO API** `/v1/todos` 하루 목록+summary · `week` · 생성 · `bulk` · 상세 · 수정 · 삭제 · `complete` · `uncomplete` · `postpone` (§5)
  선언 잠금(`DECLARED_TODO_LOCKED`), 미루기 횟수, 목표 조인(`goal_title`), 완료 시 `goal_progress` 계산까지
- **AI 입력** `/v1/ai/parse` 3단계 폴백(LLM → 규칙 파서 → 원문) + `/v1/ai/quota` (§6). LLM 은 `OPENAI_API_KEY` 가 있을 때만, 없으면 규칙 파서로
- `GET /v1/health` — PostgreSQL `SELECT 1` 과 Redis `PING` 을 실제로 확인. 하나라도 죽으면 503
- SQLAlchemy 모델 `users`, `refresh_tokens`, `goals`, `todos` + Alembic 마이그레이션 2건
- 04:00 KST 하루 경계 유틸(`app/core/time.py`)과 테스트
- Swagger UI `/v1/docs`, OpenAPI JSON `/v1/openapi.json`
- pytest 87개 (DB·Redis 없이 SQLite 로 돈다)

아직 없는 것과 임시 처리:

- `GET /todos` 의 `declaration` 은 항상 `null`, `complete` 응답의 `personal_streak` 은 항상 `0` — 선언 기능(12주차) 뒤에 채운다.
- `/todos/week` 는 전부 실시간 집계. `user_daily_stats`(10주차) 가 생기면 과거 일자는 거기서 읽는다.
- `/todos/reorder`(P1), `/ai/transcribe`(P1), `/todos/load-check`(P1), 로그인 실패 rate limit([결정 필요] A9).
- AI 쿼터는 Redis 카운터. Redis 가 없으면 카운트를 건너뛰고 무제한으로 동작한다(로그만 남김).
- `ai_parses` 로그 테이블(정확도 측정 근거)은 아직 없다. `parse_id` 는 응답에만 존재한다.
- `DELETE /goals/{id}` 는 [결정 필요 A8] 결과에 따라 지울 수 있다.

---

## 1. 사전 준비

### 1-1. Python 3.12 와 uv

```bash
python --version        # 3.12.x  (3.13 은 아직 쓰지 않는다 — 일부 의존성 wheel 확인 전)
```

`uv` 는 pip 보다 빠르고 락파일(`uv.lock`)로 팀원 간 버전을 고정한다.

```bash
# Windows PowerShell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 1-2. Docker Desktop

PostgreSQL 과 Redis 를 컨테이너로 띄운다. 팀원 사이의 버전 차이를 없애기 위해서다.

- [Docker Desktop 다운로드](https://www.docker.com/products/docker-desktop/) — 설치 후 실행해 둔 상태여야 한다.

> Docker 없이도 서버는 뜨지만 `/v1/health` 가 503 을 돌려준다. 정상이다.
> DB 가 없다는 사실을 숨기지 않고 그대로 알려 주는 것이 이 엔드포인트의 목적이다.

---

## 2. 설치부터 실행까지

```bash
git clone https://github.com/Kimtaehan2/p2j-server.git
cd p2j-server

# 의존성 — uv.lock 그대로 설치한다 (.venv 가 생긴다)
uv sync

# 환경변수
cp .env.example .env        # Windows: Copy-Item .env.example .env

# PostgreSQL 16 + Redis 7
docker compose up -d
docker compose ps           # 둘 다 (healthy)

# 마이그레이션
uv run alembic upgrade head

# 서버 — --host 0.0.0.0 을 빼면 에뮬레이터(10.0.2.2)가 못 붙는다
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

확인:

```bash
curl http://localhost:8000/v1/health
# {"data":{"status":"ok","db":"ok","redis":"ok","version":"0.1.0"}}
```

- API 문서 — <http://localhost:8000/v1/docs>
- OpenAPI JSON — <http://localhost:8000/v1/openapi.json> (Postman 은 이걸 import 한다)

정리: `docker compose down` (**`-v` 를 붙이지 않는다.** 개발 데이터가 날아간다.)

### Docker 없이 띄우기 (SQLite)

Docker 를 아직 못 깔았거나 잠깐 확인만 하려는 경우. **DB 를 SQLite 파일 하나로 바꾼다.**

```bash
# Windows PowerShell
$env:DATABASE_URL = "sqlite+aiosqlite:///./dev.db"
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
# macOS / Linux / Git Bash
DATABASE_URL="sqlite+aiosqlite:///./dev.db" uv run alembic upgrade head
DATABASE_URL="sqlite+aiosqlite:///./dev.db" uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`.env` 의 `DATABASE_URL` 을 같은 값으로 바꿔도 된다. `dev.db` 는 `.gitignore` 에 있다.

알아 둘 것:

- `/v1/health` 가 **503** 을 준다. Redis 가 없어서다. **API 는 전부 정상 동작한다** —
  Redis 는 AI 호출 횟수 세기에만 쓰고, 없으면 제한 없이 통과시킨다.
- 마이그레이션은 SQLite 에서도 그대로 돈다. PostgreSQL 출력은 달라지지 않는다.
- 팀에 공유하는 개발 데이터는 PostgreSQL 쪽에만 쌓인다. 둘은 별개 DB 다.

---

## 3. 모바일 앱과 연결하기

`p2j-mobile` 은 기본으로 `http://10.0.2.2:8000/v1` 을 본다. uvicorn 기본 포트와 같아서 **주소 지정 없이 붙는다.**

```bash
# p2j-mobile 저장소에서
flutter run --dart-define=USE_MOCK=false
# 실기기: PC 의 LAN IP
flutter run --dart-define=USE_MOCK=false --dart-define=API_BASE_URL=http://192.168.0.10:8000/v1
```

Flutter web(`flutter run -d chrome`)은 CORS 가 필요하다. 개발 환경(`APP_ENV != production`)에서는 열려 있다.

---

## 4. 자주 쓰는 명령

| 명령 | 설명 |
| --- | --- |
| `uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` | 개발 서버 |
| `uv run pytest` | 테스트 (DB·Redis 불필요) |
| `uv run ruff check .` / `uv run ruff format .` | 린트 / 포맷 — CI 와 동일 |
| `uv run mypy app` | 타입 검사 |
| `uv run alembic upgrade head` | 마이그레이션 적용 |
| `uv run alembic revision --autogenerate -m "add goals"` | 마이그레이션 생성 — **결과를 반드시 읽고 고친다** |
| `uv run alembic upgrade head --sql` | DB 없이 SQL 만 출력 (리뷰용) |
| `uv add <pkg>` / `uv add --dev <pkg>` | 의존성 추가 (`uv.lock` 이 함께 바뀐다 — 커밋한다) |

---

## 5. API 규약 (요약 — 원본은 `docs/specs/02-api-v1.md`)

### 성공

```json
{ "data": { } }
{ "data": [ ], "page": { "next_cursor": "...", "has_next": true } }
```

**204 No Content 는 감싸지 않는다.** 본문이 없다. 모바일의 `uncomplete` 가 본문 없음을 전제로 요약을 다시 계산한다.

### 실패

```json
{
  "error": {
    "code": "DECLARED_TODO_LOCKED",
    "message": "그룹에 선언한 항목은 오늘 수정할 수 없어요.",
    "details": {}
  }
}
```

- `message` 는 모바일 스낵바에 **그대로** 뜬다. 한국어 문장만. 영어·스택트레이스 금지.
- 검증 실패는 `400 VALIDATION_ERROR`, `details` 는 `{ "필드명": "메시지" }`. 중첩 목록은 `items[1].title`.
- 로그인 실패만 `INVALID_CREDENTIALS`, 토큰 문제는 `TOKEN_EXPIRED` / `UNAUTHORIZED` — 모바일이 이 코드로 재발급 여부를 정한다.
- 새 코드는 `app/core/errors.py` 의 `ERROR_CATALOG` 에 넣는다. 카탈로그에 없는 코드로 `AppError` 를 만들면 바로 실패한다.

### 날짜

- 날짜는 `YYYY-MM-DD`, 시각은 `+09:00` 오프셋을 포함한 ISO 8601 (`app/core/time.py: to_kst_iso`).
- **하루는 04:00 KST 에 바뀐다.** 오늘은 항상 `service_today()` 로 구한다. `datetime.now().date()` 를 쓰지 않는다.
- 클라이언트는 오늘을 계산하지 않고 `GET /auth/me` 의 `today` 를 믿는다.

### 코드 규칙

- **엔드포인트 파일에 로직을 쓰지 않는다.** "요청 검증 → 서비스 호출 → 응답 래핑" 세 줄이 기본. 로직은 `app/services/`.
- 보호 엔드포인트는 `CurrentUser`(= `Depends(get_current_user)`) 하나로 통일. 인증 없는 경로는 `/auth/signup`, `/auth/login`, `/auth/refresh`, `/health` 뿐.
- 응답은 dict 를 `ok()` / `paged()` 로 감싸서 반환한다. `response_model` 을 쓰지 않는다 (§14.4).
- 필드명은 snake_case 그대로. `alias_generator` 를 걸지 않는다.

---

## 6. 폴더 구조

```
p2j-server/
├─ app/
│  ├─ main.py                  앱 팩토리 · 예외 핸들러 · 미들웨어(X-Request-Id) · CORS
│  ├─ core/
│  │  ├─ config.py             Settings (.env)
│  │  ├─ errors.py             AppError · ERROR_CATALOG · Pydantic 오류 한국어 변환
│  │  ├─ response.py           ok / paged / no_content / 커서
│  │  ├─ security.py           bcrypt · access JWT · refresh 토큰 해시
│  │  ├─ deps.py               get_db · get_current_user
│  │  └─ time.py               service_today() — 04:00 KST 경계
│  ├─ db/
│  │  ├─ base.py               DeclarativeBase · naming_convention
│  │  ├─ session.py            async engine · sessionmaker
│  │  ├─ redis.py
│  │  └─ models/               users · refresh_tokens · goals · todos
│  ├─ schemas/                 응답 직렬화 함수 · Pydantic 요청 스키마
│  ├─ api/v1/
│  │  ├─ router.py
│  │  └─ endpoints/            health · auth · goals · todos · ai
│  └─ services/                비즈니스 로직 — auth · goals · todos · ai_parse · ai/(llm·rules·pipeline·quota)
├─ alembic/versions/           마이그레이션
├─ tests/                      pytest (SQLite 인메모리)
├─ docs/                       명세 원문 · 결정 기록 · 담당 영역
├─ experiments/ai/             AI 프롬프트 실험 (서버에 포함되지 않음)
├─ docker-compose.yml          PostgreSQL 16 + Redis 7
├─ Procfile                    Railway (release: alembic upgrade, web: uvicorn)
├─ pyproject.toml · uv.lock
└─ .env.example
```

---

## 7. 문서

| 문서 | 내용 |
| --- | --- |
| [`docs/specs/`](docs/specs/) | 명세 원문 4건과 충돌 해결 규칙 |
| [`docs/decisions/0003-fastapi.md`](docs/decisions/0003-fastapi.md) | **NestJS → FastAPI 전환** 결정과 대응표 |
| [`docs/architecture/module-map.md`](docs/architecture/module-map.md) | 지금 있는 모듈과 앞으로 만들 모듈 |
| [`docs/database/migration-policy.md`](docs/database/migration-policy.md) | 마이그레이션 규칙 (Alembic) |
| [`docs/ai/README.md`](docs/ai/README.md) | AI 3단계 폴백과 운영 규칙 |
| [`docs/decisions/pending-decisions.md`](docs/decisions/pending-decisions.md) | **미결 사항** — 2주차 회의 안건 |
| [`docs/OWNERSHIP.md`](docs/OWNERSHIP.md) | 담당 영역과 공동 리뷰 대상 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 브랜치 · 커밋 · PR 규칙 |

**작업을 시작하기 전에 `pending-decisions.md` 를 먼저 읽는다.** 정하지 않은 항목을 임의로 구현하면 나중에 쌓인 데이터를 손봐야 한다.

---

## 8. 문제가 생기면

### 서버가 뜨지 않고 `ValidationError ... Settings` 가 난다

`.env` 의 값이 형식에 맞지 않는다 (예: `PORT=abc`). `.env.example` 과 비교한다.
운영(`APP_ENV=production`)에서는 `JWT_SECRET` 이 기본값이면 부팅을 거부한다.

### `/v1/health` 가 503 을 준다

```json
{"error":{"code":"HEALTH_CHECK_FAILED","message":"일부 서비스에 연결할 수 없어요.","details":{"db":"down","redis":"down"}}}
```

컨테이너가 떠 있지 않다. `docker compose up -d`. Docker Desktop 자체가 꺼져 있는 경우가 가장 많다.

### 에뮬레이터에서 서버가 안 붙는다

1순위: `--host 0.0.0.0` 을 뺐는지 확인. uvicorn 기본값 `127.0.0.1` 은 `10.0.2.2` 로 들어오는 요청을 받지 못한다.

### `alembic` 이 `UnicodeDecodeError: 'cp949'` 로 죽는다

`alembic.ini` 는 OS 로캘로 읽힌다. **이 파일에는 한국어를 쓰지 않는다.** 주석도 영어로.

### 포트 8000 또는 5432 가 이미 사용 중이다

```bash
netstat -ano | findstr :8000     # Windows
lsof -i :8000                    # macOS / Linux
```

`.env` 의 `PORT` 를 바꾸면 모바일의 `API_BASE_URL` 도 함께 바꿔야 한다.
