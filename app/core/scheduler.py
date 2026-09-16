"""배치 스케줄러 (04-backend-v1 §5.9).

`@nestjs/schedule` 자리를 대신한다. 새 의존성을 넣지 않고 asyncio 태스크 하나로 돈다.
잡 개수가 셋이고 실행 시각이 하루 한 번이라 APScheduler 를 얹을 이유가 없다.

- 시각은 **항상 KST 로 계산**한다. Railway 컨테이너는 UTC 라 서버 로컬 시계를 믿으면
  04:10 이 13:10 이 된다.
- 프로세스가 죽었다 살아나도 그 시각이 지났으면 다음 날로 넘어간다. 놓친 하루는
  `python -m app.jobs --date=YYYY-MM-DD` 로 수동 보정한다.
- 인스턴스가 여러 개면 잡이 중복 실행된다. `BATCH_ENABLED=false` 로 끄고 외부 cron 을 쓴다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.core.time import KST, now_utc
from app.db.session import get_session_factory
from app.services.batch import run_daily

logger = logging.getLogger("p2j.batch")

_task: asyncio.Task[None] | None = None


def next_run_at(now: datetime, hour: int, minute: int) -> datetime:
    """다음 실행 시각(KST). 오늘 그 시각이 이미 지났으면 내일."""
    kst_now = now.astimezone(KST)
    candidate = kst_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= kst_now:
        candidate += timedelta(days=1)
    return candidate


async def _run_once() -> None:
    async with get_session_factory()() as session:
        try:
            await run_daily(session)
        except Exception:  # run_daily 가 잡별로 삼키지만 세션 자체가 깨질 수 있다
            logger.exception("배치 실행 중 복구할 수 없는 오류")
            await session.rollback()


async def _loop() -> None:
    settings = get_settings()
    while True:
        target = next_run_at(now_utc(), settings.batch_hour, settings.batch_minute)
        delay = (target - now_utc().astimezone(KST)).total_seconds()
        logger.info("다음 배치 %s (%.0f초 뒤)", target.isoformat(), delay)
        await asyncio.sleep(max(delay, 1))
        await _run_once()


def start() -> None:
    global _task
    settings = get_settings()
    if not settings.batch_enabled or settings.app_env == "test":
        logger.info("배치 스케줄러 비활성 (env=%s)", settings.app_env)
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="p2j-batch")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _task
    _task = None
