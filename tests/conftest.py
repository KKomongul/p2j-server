"""테스트 공통 픽스처.

- DB: SQLite(aiosqlite) 인메모리. PostgreSQL 전용 기능은 CI 의 서비스 컨테이너에서 확인한다.
- HTTP: httpx.AsyncClient + ASGITransport. 서버를 띄우지 않는다 (§14.10).
- Redis: /health 의 probe 를 Depends 로 교체한다. 실제 Redis 없이 돈다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 앱 import 전에 환경을 고정한다. .env 가 있어도 테스트 값이 이긴다.
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-32b"
os.environ["SERVICE_DAY_START_HOUR"] = "4"

from app.core.config import get_settings  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.db.models import Base, User  # noqa: E402
from app.main import create_app  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db_session.set_engine(engine)
    yield engine
    await engine.dispose()
    db_session.set_engine(None)


@pytest.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def clean_tables(engine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture
def app(engine):
    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions=False: 500 핸들러가 만든 응답 본문을 그대로 검증하기 위해.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def user(db: AsyncSession, clean_tables) -> User:
    row = User(
        email="taehan@p2j.dev",
        password_hash=hash_password("password123"),
        nickname="태한",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest.fixture
def make_user(db: AsyncSession, clean_tables):
    """그룹 테스트용 추가 계정 공장. (User, 인증 헤더) 를 준다."""

    async def _make(nickname: str) -> tuple[User, dict[str, str]]:
        row = User(
            email=f"{nickname}@p2j.dev",
            password_hash=hash_password("password123"),
            nickname=nickname,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        token, _ = create_access_token(row.user_id)
        return row, {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture
def access_token(user: User) -> str:
    token, _ = create_access_token(user.user_id)
    return token


@pytest.fixture
def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
