"""/uploads/* · /files/* (API 명세 §6.3).

세 갈래다.

- `POST /uploads/presign` — 서명 URL 발급. **로그인 필요.**
- `PUT  /uploads/blob/{file_key}` — 그 URL 로 실제 바이트를 받는다 (local 드라이버 전용).
- `GET  /files/{file_key}` — 저장한 사진을 돌려준다 (local 드라이버 전용).

**PUT 과 GET 은 Authorization 헤더를 요구하지 않는다.** 서명 URL 은 그 자체가
자격증명이고, 클라이언트가 남의 스토리지 호스트에 우리 토큰을 보내면 안 되기 때문이다
(Firebase 로 바꿔도 같은 코드가 돌아야 한다).

- PUT 은 HMAC 토큰 + 10분 만료로 막는다.
- GET 은 키에 든 uuid4(128비트)가 유일한 방어선이다. Firebase 의 `?alt=media&token=`
  모델과 같은 수준이며, **인가가 아니라 추측 불가능성**에 기댄다. 그룹 밖 사람이 URL 을
  건네받으면 볼 수 있다. 민감한 사진을 다루게 되면 그때 서명 조회로 바꾼다.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse

from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.core.response import ok
from app.schemas.social import PresignRequest
from app.services import uploads as svc

router = APIRouter(prefix="/uploads", tags=["uploads"])
files_router = APIRouter(prefix="/files", tags=["uploads"])


@router.post("/presign", summary="업로드용 서명 URL 발급 (10분)")
async def presign(user: CurrentUser, body: PresignRequest) -> dict[str, Any]:
    return ok(svc.presign(body.purpose, body.content_type))


@router.put(
    "/blob/{file_key:path}",
    status_code=204,
    summary="서명 URL 로 받는 실제 업로드 (인증 헤더 없음)",
)
async def upload_blob(
    request: Request,
    file_key: str,
    expires: Annotated[int, Query()],
    token: Annotated[str, Query()],
) -> Response:
    content_type = request.headers.get("content-type", "")
    # 서명에 content_type 이 들어가 있다. 헤더를 바꿔 다른 형식을 밀어 넣을 수 없다.
    if content_type not in svc.ALLOWED_CONTENT_TYPES:
        raise AppError("UNSUPPORTED_FILE_TYPE")
    svc.verify_upload_token(file_key, expires, content_type, token)

    # 헤더가 이미 상한을 넘는다고 말하면 본문을 받기 전에 끊는다.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > svc.MAX_UPLOAD_BYTES:
        raise AppError("FILE_TOO_LARGE")

    await svc.save_stream(file_key, request.stream())
    return Response(status_code=204)


@files_router.get("/{file_key:path}", summary="저장한 사진 (인증 헤더 없음)")
async def serve_file(file_key: str) -> FileResponse:
    path = svc.resolve_path(file_key)
    if not path.is_file():
        raise AppError("NOT_FOUND", "사진을 찾을 수 없어요.")

    extension = path.suffix.lstrip(".").lower()
    media_type = next(
        (t for t, ext in svc.ALLOWED_CONTENT_TYPES.items() if ext == extension),
        "application/octet-stream",
    )
    # 키가 uuid 라 내용이 바뀌지 않는다. 오래 캐시해도 안전하다.
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )
