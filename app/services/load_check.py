"""계획량 안내 (API 명세 §4 `/ai/load-check`, ERD §3.6).

"오늘 계획이 평소 소화량을 넘는지" 를 **집계 쿼리만으로** 판정한다. LLM 은 쓰지 않는다.
명세가 LLM 을 "문구를 다듬는 용도로만 선택적" 이라 적어 두었고, 판정 자체가 통계라
문구까지 모델에 맡기면 같은 근거에 다른 말이 나온다. 템플릿으로 고정했다.

하루 한 번만 계산하고 이후 조회는 저장된 행을 그대로 돌려준다 (§4, UNIQUE(user_id, date)).
아침에 "괜찮다" 고 해 놓고 점심에 "부담이다" 로 말을 바꾸지 않기 위한 것이다.
할 일을 더 넣어서 다시 보고 싶다면 다음 날 판정에 반영된다.

근거가 없으면 경고하지 않는다. 최근 14일 중 실행 기록이 7일 미만이면 `level: "ok"`,
`message: null` 로 끝낸다 (§4). 이제 막 쓰기 시작한 사용자에게 훈수를 두지 않는다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.time import now_utc, service_today, to_kst_iso
from app.db.models.daily_stat import UserDailyStat
from app.db.models.load_check import LoadCheck, new_check_id
from app.db.models.todo import Todo
from app.db.models.user import User
from app.services.stats import HEAVY_TASK_MINUTES

# 판정 창. 명세의 "지난 2주간".
WINDOW_DAYS = 14
# 근거로 인정할 최소 실행 일수. 이보다 적으면 판정하지 않는다.
MIN_RECORD_DAYS = 7
# "무거운 일을 두 개 넘게" = 3개 이상.
HEAVY_DAY_THRESHOLD = 3
# 평소 대비 이 배수를 넘으면 분량 기준으로도 경고한다.
MINUTES_WARN_RATIO = 1.5
# 한 번에 제안할 조정 항목 수 상한. 다 미루라고 하면 안내가 아니라 참견이 된다.
MAX_SUGGESTIONS = 3

_DAYS_KO = {0: "하루도 없었어요", 1: "하루", 2: "이틀", 3: "사흘", 4: "나흘"}
_COUNT_KO = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯"}


def _days_ko(n: int) -> str:
    return _DAYS_KO.get(n, f"{n}일")


def _count_ko(n: int) -> str:
    return _COUNT_KO.get(n, str(n))


# ---- 판정 -------------------------------------------------------------------------


async def _gather_evidence(db: AsyncSession, user_id: int, day: date) -> dict[str, Any]:
    """ERD §6.2 의 집계. 과거 구간은 집계표에서, 오늘 계획은 `todos` 에서 읽는다."""
    window_first = day - timedelta(days=WINDOW_DAYS)
    window_last = day - timedelta(days=1)

    row = (
        await db.execute(
            select(
                func.count(UserDailyStat.date).filter(UserDailyStat.done_count > 0),
                func.count(UserDailyStat.date).filter(
                    UserDailyStat.heavy_done_count >= HEAVY_DAY_THRESHOLD
                ),
                func.coalesce(
                    func.sum(UserDailyStat.total_actual_minutes).filter(
                        UserDailyStat.done_count > 0
                    ),
                    0,
                ),
            ).where(
                UserDailyStat.user_id == user_id,
                UserDailyStat.date >= window_first,
                UserDailyStat.date <= window_last,
            )
        )
    ).one()
    record_days, days_with_heavy, actual_total = (int(v or 0) for v in row)

    today_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(Todo.estimated_minutes), 0),
                func.count(Todo.todo_id).filter(Todo.estimated_minutes >= HEAVY_TASK_MINUTES),
                func.count(Todo.todo_id),
            ).where(Todo.user_id == user_id, Todo.date == day, Todo.deleted_at.is_(None))
        )
    ).one()
    planned_minutes, planned_heavy, planned_count = (int(v or 0) for v in today_row)

    return {
        "window_days": WINDOW_DAYS,
        "heavy_task_threshold_minutes": HEAVY_TASK_MINUTES,
        "days_with_3plus_heavy": days_with_heavy,
        "avg_completed_minutes_per_day": round(actual_total / record_days) if record_days else 0,
        "today_planned_minutes": planned_minutes,
        # 명세 예시에 없는 두 키. level 이 ok 인 이유(근거 부족인지 여유가 있어서인지)와
        # 경고 문구의 "오늘 N개" 를 클라이언트가 재계산하지 않아도 되게 함께 내려보낸다.
        "record_days": record_days,
        "today_heavy_count": planned_heavy,
        "today_planned_count": planned_count,
    }


def _judge(evidence: dict[str, Any]) -> tuple[str, str | None]:
    """(level, message). 근거가 얇으면 조용히 ok 로 끝낸다."""
    if evidence["record_days"] < MIN_RECORD_DAYS:
        return "ok", None

    heavy_today = evidence["today_heavy_count"]
    heavy_days = evidence["days_with_3plus_heavy"]
    average = evidence["avg_completed_minutes_per_day"]
    planned = evidence["today_planned_minutes"]

    if heavy_today >= HEAVY_DAY_THRESHOLD and heavy_days < HEAVY_DAY_THRESHOLD:
        return "warning", (
            f"지난 {WINDOW_DAYS}일간 한 시간 이상 걸리는 일을 두 개 넘게 끝낸 날은 "
            f"{_days_ko(heavy_days)}뿐이었어요. "
            f"오늘 {_count_ko(heavy_today)} 개는 부담일 수 있어요."
        )

    if average > 0 and planned > average * MINUTES_WARN_RATIO:
        return "warning", (
            f"평소에는 하루 {average}분쯤 끝냈는데 오늘 계획은 {planned}분이에요. "
            "몇 개는 내일로 미뤄도 괜찮아요."
        )

    return "ok", None


async def _suggestions(
    db: AsyncSession, user_id: int, day: date, evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    """평소 분량 아래로 내려갈 만큼만 미루기를 제안한다. 가장 늦게 추가한 것부터."""
    average = evidence["avg_completed_minutes_per_day"]
    planned = evidence["today_planned_minutes"]
    if average <= 0 or planned <= average:
        return []

    rows = list(
        await db.scalars(
            select(Todo)
            .where(
                Todo.user_id == user_id,
                Todo.date == day,
                Todo.deleted_at.is_(None),
                Todo.status == "pending",
                # 선언한 항목은 미룰 수 없다 (§6.2). 제안 목록에서도 뺀다.
                Todo.declared_at.is_(None),
            )
            .order_by(Todo.todo_id.desc())
        )
    )

    tomorrow = (day + timedelta(days=1)).isoformat()
    out: list[dict[str, Any]] = []
    remaining = planned
    for todo in rows:
        if remaining <= average or len(out) >= MAX_SUGGESTIONS:
            break
        out.append(
            {
                "todo_id": todo.todo_id,
                "action": "defer",
                "to_date": tomorrow,
                "reason": "가장 늦게 추가된 항목",
            }
        )
        remaining -= todo.estimated_minutes or 0
    return out


# ---- 조회·응답 ---------------------------------------------------------------------


def to_dict(check: LoadCheck) -> dict[str, Any]:
    return {
        "check_id": check.check_id,
        "date": check.date.isoformat(),
        "level": check.level,
        "message": check.message,
        "evidence": check.evidence_json,
        "suggestions": check.suggestions_json,
        "accepted": check.accepted,
        "applied_todo_ids": check.applied_todo_ids,
        "responded_at": to_kst_iso(check.responded_at),
    }


async def get_or_create(db: AsyncSession, user: User, day: date | None) -> dict[str, Any]:
    target = day or service_today()

    existing = await db.scalar(
        select(LoadCheck).where(LoadCheck.user_id == user.user_id, LoadCheck.date == target)
    )
    if existing is not None:
        return to_dict(existing)

    evidence = await _gather_evidence(db, user.user_id, target)
    level, message = _judge(evidence)
    suggestions = (
        await _suggestions(db, user.user_id, target, evidence) if level == "warning" else []
    )

    check = LoadCheck(
        check_id=new_check_id(),
        user_id=user.user_id,
        date=target,
        level=level,
        message=message,
        evidence_json=evidence,
        suggestions_json=suggestions,
        applied_todo_ids=[],
    )
    db.add(check)
    await db.flush()
    await db.refresh(check)
    return to_dict(check)


async def respond(
    db: AsyncSession,
    user: User,
    check_id: str,
    *,
    accepted: bool,
    applied_todo_ids: list[int],
) -> dict[str, Any]:
    """사용자가 안내를 받아들였는지 기록한다.

    할 일을 실제로 미루는 건 `POST /todos/{id}/defer` 가 한다. 여기서는 결과만 남긴다
    (ERD 의 `applied_todo_ids` = "실제 미룬 항목"). 수락률이 곧 이 기능의 유효성 지표다.
    """
    check = await db.scalar(
        select(LoadCheck).where(
            LoadCheck.check_id == check_id, LoadCheck.user_id == user.user_id
        )
    )
    if check is None:
        raise AppError("LOAD_CHECK_NOT_FOUND")

    check.accepted = accepted
    check.applied_todo_ids = list(dict.fromkeys(applied_todo_ids))
    check.responded_at = now_utc()
    await db.flush()
    await db.refresh(check)
    return to_dict(check)
