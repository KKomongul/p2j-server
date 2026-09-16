"""/stats/* (API 명세 §7). 로직은 services/stats.py."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.core.response import ok
from app.services import stats as svc

router = APIRouter(prefix="/stats", tags=["stats"])

Period = Literal["week", "month"]


@router.get("/summary", summary="기간별 달성률·연속 기록·시간대 분포")
async def summary(user: CurrentUser, db: DbSession, period: Period = "week") -> dict[str, Any]:
    return ok(await svc.summary(db, user, period))


@router.get("/goals/{goal_id}", summary="개별 목표의 기간별 달성 추이")
async def goal_stats(
    user: CurrentUser, db: DbSession, goal_id: int, period: Period = "week"
) -> dict[str, Any]:
    return ok(await svc.goal_stats(db, user, goal_id, period))
