"""업로드 (API 명세 §6.3 `POST /uploads/presign`).

클라이언트 계약은 스토리지가 어디든 똑같다.

    POST /uploads/presign  →  { upload_url, file_key }
    PUT  <upload_url>      →  바이트를 그대로
    POST /groups/{id}/proofs  { file_key }

바뀌는 건 `upload_url` 이 어디를 가리키느냐뿐이라, 드라이버를 갈아끼워도
모바일 코드는 한 줄도 손대지 않는다.

| STORAGE_DRIVER | upload_url | 파일이 사는 곳 |
| --- | --- | --- |
| `local` (기본) | 이 서버의 `/uploads/blob/...` (상대 경로) | `STORAGE_DIR` 아래 |
| `firebase` | Firebase 서명 URL (절대) | Cloud Storage 버킷 |

**기본값이 `local` 인 이유.** 명세(§6.3)는 파일이 서버를 거치지 않는 쪽을 택했고
그게 맞다 — 다만 그러려면 Firebase 프로젝트와 Blaze 요금제가 먼저 있어야 한다.
그때까지 인증샷 기능 전체가 멈춰 있는 것보다, 같은 계약으로 서버가 받아 두고
나중에 `STORAGE_DRIVER=firebase` 한 줄로 옮기는 편이 낫다.
이번 학기 규모(3~6명 × 하루 몇 장)에서 서버 부담은 무시할 수 있다.

**local 드라이버의 한계.** 로그인한 사용자가 presign 을 반복하면 10MB 짜리를 계속
올릴 수 있다. 한 건 상한만 있고 사용자별 총량 제한은 없다. 3~6명 규모에서는 문제가
아니지만, 공개 시범에 들어가면 쿼터(Redis 카운터)나 Firebase 전환이 먼저다.

**서명 URL 은 그 자체가 자격증명이다.** local 드라이버도 HMAC 토큰과 만료를 넣어
아무나 PUT 할 수 없게 한다. 인증 헤더는 요구하지 않는다 — 클라이언트가 남의
스토리지 호스트에 우리 토큰을 보내면 안 되는데, 드라이버마다 다르게 굴면 그 규칙이 깨진다.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import timedelta
from pathlib import Path
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

# FILE_TOO_LARGE 의 기준과 같은 값 (errors.py 의 문구가 10MB 라고 말한다).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def build_file_key(purpose: str, content_type: str) -> str:
    """`proofs/2026/09/15/{uuid}.jpg`.

    날짜 경로는 보관 정책과 수동 정리를 쉽게 하려는 것이고, uuid4 는 키를 추측할 수
    없게 만든다. 조회 URL 의 유일한 방어선이라 순번 같은 걸 쓰면 안 된다.
    """
    day = service_today()
    extension = ALLOWED_CONTENT_TYPES[content_type]
    folder = "proofs" if purpose == "proof" else "profiles"
    return f"{folder}/{day:%Y/%m/%d}/{uuid.uuid4().hex}.{extension}"


def _driver() -> str:
    return get_settings().storage_driver


# ---- 서명 -------------------------------------------------------------------------


def _sign(file_key: str, expires: int, content_type: str) -> str:
    """local 드라이버의 업로드 토큰. JWT 비밀키를 재사용한다.

    운영에서 JWT_SECRET 이 바뀌면 발급 중이던 업로드 URL 도 같이 죽는다.
    유효기간이 10분이라 실질적인 문제가 되지 않는다.
    """
    secret = get_settings().jwt_secret.encode()
    message = f"{file_key}|{expires}|{content_type}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:32]


def verify_upload_token(
    file_key: str, expires: int, content_type: str, token: str
) -> None:
    """PUT 을 받을 때의 검증. 실패는 전부 403 으로 뭉뚱그린다.

    "만료됐다" 와 "서명이 틀렸다" 를 구분해 알려 주면 토큰을 맞춰 볼 여지를 준다.
    """
    if expires < int(now_utc().timestamp()):
        raise AppError("FORBIDDEN", "업로드 링크가 만료됐어요. 다시 시도해 주세요.")
    if not hmac.compare_digest(_sign(file_key, expires, content_type), token):
        raise AppError("FORBIDDEN")


# ---- 경로 -------------------------------------------------------------------------


def storage_root() -> Path:
    root = Path(get_settings().storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_path(file_key: str) -> Path:
    """저장 키 → 실제 경로. 경로 탈출을 막는다.

    `..` 이 섞인 키가 오면 STORAGE_DIR 밖으로 쓸 수 있다. 키는 서버가 만들지만
    PUT 의 경로 파라미터로 다시 들어오므로 여기서 한 번 더 막는다.
    """
    root = storage_root().resolve()
    target = (root / file_key).resolve()
    if not target.is_relative_to(root):
        raise AppError("FORBIDDEN")
    return target


# ---- 조회 URL ---------------------------------------------------------------------


def public_url(file_key: str | None) -> str | None:
    """저장 키 → 내려받기 URL.

    local 은 **상대 경로**(`/files/...`)를 준다. 서버는 자기 공개 주소를 모르는데
    (에뮬레이터는 10.0.2.2, 배포는 도메인) 모바일은 API base URL 을 이미 알고 있다.
    거기에 이어 붙이면 된다. Firebase 는 절대 URL 이라 그대로 나간다.
    """
    if not file_key:
        return None
    if _driver() == "firebase":
        bucket = get_settings().firebase_storage_bucket
        if not bucket:
            return None
        return (
            f"https://firebasestorage.googleapis.com/v0/b/{bucket}"
            f"/o/{quote(file_key, safe='')}?alt=media"
        )
    return f"/files/{file_key}"


# ---- Firebase --------------------------------------------------------------------

_bucket: Any = None


def _storage_bucket() -> Any:
    """firebase-admin 앱을 한 번만 띄운다. 자격증명이 없으면 503."""
    global _bucket
    if _bucket is not None:
        return _bucket

    settings = get_settings()
    if not settings.firebase_credentials_path or not settings.firebase_storage_bucket:
        raise AppError(
            "STORAGE_UNAVAILABLE",
            "사진 저장소가 아직 설정되지 않았어요.",
        )

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


def reset_firebase_cache() -> None:
    """테스트에서 드라이버를 바꿔 끼울 때 쓴다."""
    global _bucket
    _bucket = None


def _firebase_presign(file_key: str, content_type: str) -> str:
    blob = _storage_bucket().blob(file_key)
    # v4 서명은 로컬 키로만 계산한다. 네트워크 호출이 없어 동기로 불러도 막히지 않는다.
    return str(
        blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=UPLOAD_URL_TTL_MINUTES),
            method="PUT",
            content_type=content_type,
        )
    )


# ---- presign ---------------------------------------------------------------------


def presign(purpose: str, content_type: str) -> dict[str, Any]:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise AppError("UNSUPPORTED_FILE_TYPE")
    if purpose not in PURPOSES:
        raise AppError("VALIDATION_ERROR", details={"purpose": "선택할 수 없는 값이에요."})

    file_key = build_file_key(purpose, content_type)
    expires_at = now_utc() + timedelta(minutes=UPLOAD_URL_TTL_MINUTES)

    if _driver() == "firebase":
        upload_url = _firebase_presign(file_key, content_type)
    else:
        stamp = int(expires_at.timestamp())
        token = _sign(file_key, stamp, content_type)
        upload_url = f"/uploads/blob/{file_key}?expires={stamp}&token={token}"

    return {
        "upload_url": upload_url,
        "file_key": file_key,
        "content_type": content_type,
        "max_bytes": MAX_UPLOAD_BYTES,
        "expires_at": to_kst_iso(expires_at),
    }


# ---- 저장 -------------------------------------------------------------------------


async def save_stream(file_key: str, chunks: Any) -> int:
    """PUT 본문을 그대로 파일에 쓴다. 상한을 넘으면 쓰다 만 파일을 지운다.

    Content-Length 를 믿지 않고 실제로 받은 바이트로 센다. 헤더는 거짓말할 수 있다.
    """
    target = resolve_path(file_key)
    target.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    try:
        with target.open("wb") as out:
            async for chunk in chunks:
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise AppError("FILE_TOO_LARGE")
                out.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    if written == 0:
        target.unlink(missing_ok=True)
        raise AppError("VALIDATION_ERROR", details={"body": "빈 파일은 올릴 수 없어요."})
    return written
