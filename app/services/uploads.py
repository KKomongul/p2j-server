"""업로드 presign (API 명세 §6.3 `POST /uploads/presign`).

이미지는 서버를 거치지 않는다. 클라이언트가 여기서 받은 URL 로 스토리지에 직접 올리고
`file_key` 만 API 로 넘긴다. 사진 한 장이 수 MB 인데 서버가 중계하면 Railway 컨테이너의
메모리와 대역폭이 먼저 터진다.

**스토리지 자격증명이 없으면 503 STORAGE_UNAVAILABLE 을 낸다.** 서버는 뜬다.
`FIREBASE_CREDENTIALS_PATH` · `FIREBASE_STORAGE_BUCKET` 이 채워지기 전까지 이 엔드포인트만
막히고, 나머지 그룹·선언·피드 기능은 `file_key` 를 직접 넣어도 그대로 동작한다.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.time import now_utc, service_today, to_kst_iso

# 명세 §1.9 의 허용 형식. 확장자는 저장 키를 만들 때만 쓴다.
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
}
UPLOAD_URL_TTL_MINUTES = 10
PURPOSES = ("proof", "profile")


def build_file_key(purpose: str, content_type: str) -> str:
    """`proofs/2026/09/15/{uuid}.jpg`. 날짜 경로는 보관 정책과 수동 정리를 쉽게 하려는 것."""
    day = service_today()
    extension = ALLOWED_CONTENT_TYPES[content_type]
    folder = "proofs" if purpose == "proof" else "profiles"
    return f"{folder}/{day:%Y/%m/%d}/{uuid.uuid4().hex}.{extension}"


def public_url(file_key: str | None) -> str | None:
    """저장 키 → 내려받기 URL. 버킷이 설정되지 않았으면 null 을 준다.

    모바일은 `image_url` 이 null 이면 자리표시자를 그린다. `file_key` 는 언제나 함께
    내려가므로, 버킷을 나중에 붙여도 기존 게시물이 그대로 살아난다.
    """
    bucket = get_settings().firebase_storage_bucket
    if not bucket or not file_key:
        return None
    return (
        f"https://firebasestorage.googleapis.com/v0/b/{bucket}"
        f"/o/{quote(file_key, safe='')}?alt=media"
    )


_bucket: Any = None


def _storage_bucket() -> Any:
    """firebase-admin 앱을 한 번만 띄운다. 자격증명이 없으면 503."""
    global _bucket
    if _bucket is not None:
        return _bucket

    settings = get_settings()
    if not settings.firebase_credentials_path or not settings.firebase_storage_bucket:
        raise AppError("STORAGE_UNAVAILABLE", "사진 업로드가 아직 설정되지 않았어요.")

    try:
        import firebase_admin
        from firebase_admin import credentials, storage

        if not firebase_admin._apps:
            firebase_admin.initialize_app(
                credentials.Certificate(settings.firebase_credentials_path),
                {"storageBucket": settings.firebase_storage_bucket},
            )
        _bucket = storage.bucket()
    except AppError:
        raise
    except Exception as exc:  # 자격증명 파일이 깨졌거나 버킷이 없는 경우
        raise AppError("STORAGE_UNAVAILABLE") from exc
    return _bucket


def presign(purpose: str, content_type: str) -> dict[str, Any]:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise AppError("UNSUPPORTED_FILE_TYPE")
    if purpose not in PURPOSES:
        raise AppError("VALIDATION_ERROR", details={"purpose": "선택할 수 없는 값이에요."})

    file_key = build_file_key(purpose, content_type)
    expires_at = now_utc() + timedelta(minutes=UPLOAD_URL_TTL_MINUTES)

    blob = _storage_bucket().blob(file_key)
    # v4 서명은 로컬 키로만 계산한다. 네트워크 호출이 없어 동기로 불러도 이벤트 루프를 막지 않는다.
    upload_url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(minutes=UPLOAD_URL_TTL_MINUTES),
        method="PUT",
        content_type=content_type,
    )

    return {
        "upload_url": upload_url,
        "file_key": file_key,
        "content_type": content_type,
        "expires_at": to_kst_iso(expires_at),
    }
