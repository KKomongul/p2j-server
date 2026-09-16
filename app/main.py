"""FastAPI 앱. 라우터 등록, 예외 핸들러, 미들웨어.

실행:  uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
       (--host 0.0.0.0 이 없으면 에뮬레이터의 10.0.2.2 가 붙지 못한다)
문서:  http://localhost:8000/v1/docs   OpenAPI: /v1/openapi.json
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    pydantic_error_to_message,
    pydantic_loc_to_field,
)
from app.core.scheduler import start as start_scheduler
from app.core.scheduler import stop as stop_scheduler
from app.db.redis import close_redis
from app.db.session import dispose_engine

API_PREFIX = "/v1"
REQUEST_ID_HEADER = "X-Request-Id"

logger = logging.getLogger("p2j")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("P2J 서버 시작 (env=%s, port=%s)", settings.app_env, settings.port)
    start_scheduler()  # 매일 04:10 KST 배치 (§5.9). 테스트·BATCH_ENABLED=false 면 뜨지 않는다.
    yield
    await stop_scheduler()
    await dispose_engine()
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="P2J API",
        version="v1",
        description=(
            "P2J 서버 API. 성공은 `{ data }` (목록은 `{ data, page }`), "
            "실패는 `{ error: { code, message, details } }` 형식이다. "
            "계약의 원본은 docs/specs/02-api-v1.md."
        ),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )

    _configure_cors(app, settings)
    _register_middleware(app)
    _register_exception_handlers(app)

    app.include_router(api_router, prefix=API_PREFIX)
    return app


# ---- CORS ----------------------------------------------------------------------


def _configure_cors(app: FastAPI, settings: Any) -> None:
    # 모바일 앱은 CORS 가 필요 없다. Flutter web(flutter run -d chrome) 개발용 (§11).
    if settings.is_production:
        origins = settings.cors_origin_list
        if not origins:
            return
    else:
        origins = ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )


# ---- 미들웨어 -------------------------------------------------------------------


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 요청 ID 를 응답 헤더로 돌려준다 (§11). 본문·토큰은 로그에 남기지 않는다.
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


# ---- 예외 핸들러 -----------------------------------------------------------------
# FastAPI 기본 검증 오류는 {"detail": [...]} 라서 모바일이 파싱하지 못한다. 전부 덮어쓴다.


def _error_response(request: Request, error: AppError) -> JSONResponse:
    response = JSONResponse(status_code=error.status, content=error.to_body())
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 필드명 → 한국어 메시지 (§1.2). 같은 필드에 오류가 여럿이면 첫 번째만.
        details: dict[str, str] = {}
        for item in exc.errors():
            field = pydantic_loc_to_field(tuple(item.get("loc", ())))
            details.setdefault(field, pydantic_error_to_message(item))
        return _error_response(request, AppError("VALIDATION_ERROR", details=details))

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 라우터가 던지는 404/405 와 fastapi.HTTPException 을 우리 형식으로.
        if exc.status_code == 404:
            error = AppError(
                "NOT_FOUND", details={"method": request.method, "path": request.url.path}
            )
        elif exc.status_code == 405:
            error = AppError("METHOD_NOT_ALLOWED")
        elif exc.status_code == 401:
            error = AppError("UNAUTHORIZED")
        elif exc.status_code == 403:
            error = AppError("FORBIDDEN")
        elif exc.status_code == 413:
            error = AppError("FILE_TOO_LARGE")
        elif exc.status_code >= 500:
            error = AppError("INTERNAL_ERROR")
        else:
            error = AppError("VALIDATION_ERROR", status=exc.status_code)
        return _error_response(request, error)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # 스택·환경변수·DB URL 은 응답에 넣지 않는다. 서버 로그에만 남긴다.
        logger.exception(
            "처리되지 않은 오류 request_id=%s path=%s",
            getattr(request.state, "request_id", "-"),
            request.url.path,
        )
        return _error_response(request, AppError("INTERNAL_ERROR"))


app = create_app()
