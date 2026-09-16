"""/uploads/* (API 명세 §6.3).

사진은 서버를 거치지 않는다. 여기서 서명된 URL 만 발급하고, 업로드는 클라이언트가
스토리지에 직접 한다.

스토리지 자격증명이 없으면 **503 STORAGE_UNAVAILABLE** 이다. 서버는 정상적으로 뜨고
이 엔드포인트만 막힌다 (services/uploads.py 참고).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.deps import CurrentUser
from app.core.response import ok
from app.schemas.social import PresignRequest
from app.services import uploads as svc

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/presign", summary="업로드용 서명 URL 발급 (10분)")
async def presign(user: CurrentUser, body: PresignRequest) -> dict[str, Any]:
    return ok(svc.presign(body.purpose, body.content_type))
