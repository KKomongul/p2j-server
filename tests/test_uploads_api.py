"""/uploads/* · /files/* — 업로드 왕복과 서명 검증 (명세 §6.3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.services import uploads as svc

# 1x1 PNG. 실제 바이트여야 FileResponse 가 내려주는 것까지 확인할 수 있다.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def storage_dir(tmp_path: Path):
    """테스트마다 빈 디렉터리. 실제 작업 폴더를 더럽히지 않는다."""
    settings = get_settings()
    original = settings.storage_dir
    settings.storage_dir = str(tmp_path / "uploads")
    yield tmp_path
    settings.storage_dir = original


async def _presign(client: AsyncClient, headers: dict[str, str], **body) -> dict:
    payload = {"content_type": "image/png", "purpose": "proof", **body}
    response = await client.post("/v1/uploads/presign", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ---- presign ---------------------------------------------------------------------


async def test_presign_returns_relative_url_for_local_driver(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """local 은 상대 경로를 준다. 서버는 자기 공개 주소를 모른다."""
    data = await _presign(client, auth_headers)

    assert data["upload_url"].startswith("/uploads/blob/proofs/")
    assert "token=" in data["upload_url"] and "expires=" in data["upload_url"]
    assert data["file_key"].startswith("proofs/")
    assert data["file_key"].endswith(".png")
    assert data["content_type"] == "image/png"
    assert data["max_bytes"] == svc.MAX_UPLOAD_BYTES
    assert data["expires_at"] is not None


async def test_presign_rejects_unsupported_type(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/v1/uploads/presign",
        json={"content_type": "application/pdf"},
        headers=auth_headers,
    )
    assert response.status_code == 400  # Literal 위반이라 Pydantic 이 먼저 잡는다
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_presign_requires_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/uploads/presign", json={"content_type": "image/png"}
    )
    assert response.status_code == 401


async def test_file_keys_are_unguessable(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """키가 조회 URL 의 유일한 방어선이다. 순번이면 남의 사진이 뚫린다."""
    first = await _presign(client, auth_headers)
    second = await _presign(client, auth_headers)
    assert first["file_key"] != second["file_key"]
    # proofs/YYYY/MM/DD/<32 hex>.png
    name = first["file_key"].rsplit("/", 1)[-1].removesuffix(".png")
    assert len(name) == 32


# ---- 업로드 왕복 -------------------------------------------------------------------


async def test_upload_then_serve(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    data = await _presign(client, auth_headers)

    # 업로드는 인증 헤더 없이 간다. 서명 URL 자체가 자격증명이다.
    put = await client.put(
        f"/v1{data['upload_url']}",
        content=PNG,
        headers={"Content-Type": "image/png"},
    )
    assert put.status_code == 204, put.text

    got = await client.get(f"/v1/files/{data['file_key']}")
    assert got.status_code == 200
    assert got.content == PNG
    assert got.headers["content-type"].startswith("image/png")
    assert "immutable" in got.headers["cache-control"]


async def test_upload_rejects_tampered_token(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    data = await _presign(client, auth_headers)
    tampered = data["upload_url"].replace("token=", "token=x")

    put = await client.put(
        f"/v1{tampered}", content=PNG, headers={"Content-Type": "image/png"}
    )
    assert put.status_code == 403
    assert put.json()["error"]["code"] == "FORBIDDEN"


async def test_upload_rejects_expired_link(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    data = await _presign(client, auth_headers)
    file_key = data["file_key"]
    # 이미 지난 시각으로 제대로 서명한 링크. 서명은 맞지만 만료됐다.
    expired = 1_700_000_000
    token = svc._sign(file_key, expired, "image/png")

    put = await client.put(
        f"/v1/uploads/blob/{file_key}?expires={expired}&token={token}",
        content=PNG,
        headers={"Content-Type": "image/png"},
    )
    assert put.status_code == 403


async def test_upload_rejects_content_type_swap(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """서명에 content_type 이 들어 있다. 헤더만 바꿔 다른 형식을 밀어 넣을 수 없다."""
    data = await _presign(client, auth_headers, content_type="image/png")

    put = await client.put(
        f"/v1{data['upload_url']}",
        content=PNG,
        headers={"Content-Type": "image/jpeg"},
    )
    assert put.status_code == 403


async def test_upload_rejects_oversize_declaration(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    data = await _presign(client, auth_headers)
    put = await client.put(
        f"/v1{data['upload_url']}",
        content=PNG,
        headers={
            "Content-Type": "image/png",
            "Content-Length": str(svc.MAX_UPLOAD_BYTES + 1),
        },
    )
    assert put.status_code == 413
    assert put.json()["error"]["code"] == "FILE_TOO_LARGE"


async def test_upload_rejects_empty_body(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    data = await _presign(client, auth_headers)
    put = await client.put(
        f"/v1{data['upload_url']}",
        content=b"",
        headers={"Content-Type": "image/png"},
    )
    assert put.status_code == 400


async def test_missing_file_is_404(client: AsyncClient) -> None:
    response = await client.get("/v1/files/proofs/2026/09/16/deadbeef.png")
    assert response.status_code == 404


# ---- 경로 탈출 ---------------------------------------------------------------------


def test_resolve_path_blocks_escape(storage_dir: Path) -> None:
    """키는 서버가 만들지만 PUT 의 경로 파라미터로 다시 들어온다."""
    from app.core.errors import AppError

    for evil in (
        "../../etc/passwd",
        "proofs/../../../secrets.env",
        "/etc/passwd",
        "C:/Windows/System32/config/SAM",
    ):
        with pytest.raises(AppError) as exc:
            svc.resolve_path(evil)
        assert exc.value.code == "FORBIDDEN", evil

    inside = svc.resolve_path("proofs/2026/09/16/ok.png")
    assert inside.is_relative_to(svc.storage_root().resolve())


# ---- 조회 URL ---------------------------------------------------------------------


def test_public_url_shapes() -> None:
    settings = get_settings()
    original = settings.storage_driver
    try:
        settings.storage_driver = "local"
        assert svc.public_url("proofs/a.jpg") == "/files/proofs/a.jpg"
        assert svc.public_url(None) is None

        settings.storage_driver = "firebase"
        settings.firebase_storage_bucket = "p2j.appspot.com"
        url = svc.public_url("proofs/a.jpg")
        assert url is not None
        assert url.startswith("https://firebasestorage.googleapis.com/")
        assert "proofs%2Fa.jpg" in url  # 키가 통째로 인코딩된다
    finally:
        settings.storage_driver = original
        settings.firebase_storage_bucket = ""
