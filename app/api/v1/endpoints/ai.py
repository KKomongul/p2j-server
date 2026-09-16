"""/ai/* (API 명세 §6 파싱, §4 계획량 안내).

`/ai/parse` 는 저장하지 않는다 — 저장은 `POST /todos/bulk`.
`/ai/load-check` 는 반대로 판정 결과를 저장한다. 하루 한 번만 계산하고 이후 조회는
같은 행을 돌려준다 (§4). 아침에 한 말을 점심에 바꾸지 않기 위해서다.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.core.response import ok
from app.schemas.ai import ParseRequest
from app.schemas.load_check import LoadCheckResponseRequest
from app.services import ai_parse as svc
from app.services import load_check as load_svc

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/parse", summary="자유 텍스트 → TODO 초안 목록 (3단계 폴백, 저장 안 함)")
async def parse(user: CurrentUser, db: DbSession, body: ParseRequest) -> dict[str, Any]:
    return ok(await svc.parse(db, user, body))


@router.get("/quota", summary="오늘 남은 AI 호출 수")
async def quota(user: CurrentUser) -> dict[str, Any]:
    return ok(await svc.quota_state(user))


@router.get("/load-check", summary="오늘 계획이 평소 소화량을 넘는지 (집계 기반, LLM 미사용)")
async def load_check(
    user: CurrentUser, db: DbSession, date: date_type | None = None
) -> dict[str, Any]:
    return ok(await load_svc.get_or_create(db, user, date))


@router.post("/load-check/{check_id}/response", summary="안내를 받아들였는지 기록")
async def load_check_response(
    user: CurrentUser, db: DbSession, check_id: str, body: LoadCheckResponseRequest
) -> dict[str, Any]:
    return ok(
        await load_svc.respond(
            db, user, check_id, accepted=body.accepted, applied_todo_ids=body.applied_todo_ids
        )
    )
