"""하루 한 번 도는 배치 잡 (04-backend-v1 §5.9).

하루가 04:00 KST 에 바뀌므로(BR-01) 배치는 그 직후인 **04:10 KST** 에 돈다.
전날이 완전히 닫힌 뒤에 판정해야 "새벽 3시에 끝낸 일" 이 어제 성과로 잡힌다.

| 잡 | 내용 |
| --- | --- |
| 집계 마감 | 전일 `user_daily_stats` 재계산 (그날 바뀐 사용자만) |
| 그룹 스트릭 | 전일 대상 판정 (ERD §6.3) |
| 토큰 정리 | 만료 7일 지난 refresh token 삭제 |

각 잡은 **여러 번 돌려도 결과가 같다**. 재시도·수동 실행·인스턴스 재시작에 안전하다.
한 잡이 실패해도 나머지는 계속 돈다 — 토큰 정리가 막혔다고 스트릭까지 멈출 이유가 없다.

Railway 인스턴스가 1개일 때만 안전하다(같은 잡이 중복 실행된다). 이번 학기는 단일 인스턴스로
고정한다. 스케일 아웃하면 `app/core/scheduler.py` 를 끄고 외부 cron 에서
`python -m app.jobs` 를 부르면 된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now_utc, service_today
from app.db.models.group import Group
from app.db.models.refresh_token import RefreshToken
from app.db.models.todo import Todo
from app.services import ranking as ranking_svc
from app.services import stats as stats_svc

logger = logging.getLogger("p2j.batch")

# 만료 뒤에도 이 기간만큼은 남긴다. 재사용 감지 추적에 쓰인다 (auth §1.6).
TOKEN_RETENTION_DAYS = 7


@dataclass
class BatchReport:
    target_date: date
    users_recalculated: int = 0
    groups_evaluated: int = 0
    groups_succeeded: int = 0
    tokens_deleted: int = 0
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_date": self.target_date.isoformat(),
            "users_recalculated": self.users_recalculated,
            "groups_evaluated": self.groups_evaluated,
            "groups_succeeded": self.groups_succeeded,
            "tokens_deleted": self.tokens_deleted,
            "failures": self.failures,
        }


# ---- 개별 잡 -----------------------------------------------------------------------


async def close_daily_stats(db: AsyncSession, day: date) -> int:
    """전일 집계를 확정한다.

    평소에는 todo 를 만질 때마다 즉시 반영되므로 여기서 바뀌는 값은 보통 없다.
    이 잡의 역할은 **연속 기록 컬럼을 앞에서부터 다시 맞추는 것**이다. 과거 날짜를
    뒤늦게 고치면 그 뒤 날들의 `streak_count` 가 낡는데, 하루 한 번 여기서 정리된다.
    """
    user_ids = list(
        await db.scalars(select(Todo.user_id).where(Todo.date == day).distinct())
    )
    for user_id in user_ids:
        await stats_svc.recalc_day(db, int(user_id), day)
    return len(user_ids)


async def evaluate_group_streaks(db: AsyncSession, day: date) -> tuple[int, int]:
    """(판정한 그룹 수, 성공한 그룹 수). ERD §6.3 의 규칙."""
    groups = list(await db.scalars(select(Group).where(Group.deleted_at.is_(None))))
    succeeded = 0
    for group in groups:
        if await ranking_svc.evaluate_streak(db, group, day):
            succeeded += 1
    return len(groups), succeeded


async def purge_expired_tokens(db: AsyncSession) -> int:
    cutoff = now_utc() - timedelta(days=TOKEN_RETENTION_DAYS)
    # 지울 대상을 먼저 세고 지운다. CursorResult.rowcount 는 드라이버마다 신뢰도가 다르다.
    doomed = await db.scalar(
        select(func.count(RefreshToken.token_id)).where(RefreshToken.expires_at < cutoff)
    )
    await db.execute(delete(RefreshToken).where(RefreshToken.expires_at < cutoff))
    return int(doomed or 0)


# ---- 오케스트레이션 -----------------------------------------------------------------


async def run_daily(db: AsyncSession, target_date: date | None = None) -> BatchReport:
    """전일(기본) 대상으로 모든 잡을 돌린다. 잡 하나가 실패해도 나머지는 계속한다."""
    day = target_date or (service_today() - timedelta(days=1))
    report = BatchReport(target_date=day)

    try:
        report.users_recalculated = await close_daily_stats(db, day)
    except Exception as exc:
        logger.exception("집계 마감 실패 date=%s", day)
        report.failures.append(f"close_daily_stats: {exc!r}")
        await db.rollback()

    try:
        report.groups_evaluated, report.groups_succeeded = await evaluate_group_streaks(db, day)
    except Exception as exc:
        logger.exception("그룹 스트릭 판정 실패 date=%s", day)
        report.failures.append(f"evaluate_group_streaks: {exc!r}")
        await db.rollback()

    try:
        report.tokens_deleted = await purge_expired_tokens(db)
    except Exception as exc:
        logger.exception("토큰 정리 실패")
        report.failures.append(f"purge_expired_tokens: {exc!r}")
        await db.rollback()

    await db.commit()
    logger.info("배치 완료 %s", report.to_dict())
    return report
