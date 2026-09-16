"""계획량 안내 요청 스키마 (API 명세 §4)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoadCheckResponseRequest(BaseModel):
    """`POST /ai/load-check/{check_id}/response`.

    `applied_todo_ids` 는 **이미 미룬** 항목이다. 실제 미루기는 `/todos/{id}/defer` 가 한다.
    여기서는 받아들였는지 여부만 쌓는다 — 이 분포가 다음 판정의 근거가 된다 (§4).
    """

    accepted: bool
    applied_todo_ids: list[int] = Field(default_factory=list, max_length=20)
