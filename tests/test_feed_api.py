"""/groups/{id}/feed · /posts/* — 인증샷·댓글·리액션 (명세 §6.3~§6.4)."""

from httpx import AsyncClient

FILE_KEY = "proofs/2026/09/16/abc123.jpg"


async def _group_with(client: AsyncClient, owner: dict[str, str], *others: dict[str, str]) -> dict:
    group = (
        await client.post("/v1/groups", json={"name": "3학년 개발조"}, headers=owner)
    ).json()["data"]
    for headers in others:
        r = await client.post(
            "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=headers
        )
        assert r.status_code == 200, r.text
    return group


async def _declare(
    client: AsyncClient, headers: dict[str, str], group_id: int, *titles: str
) -> dict:
    todo_ids = []
    for title in titles:
        r = await client.post("/v1/todos", json={"title": title}, headers=headers)
        todo_ids.append(r.json()["data"]["todo_id"])
    r = await client.post(
        f"/v1/groups/{group_id}/declarations", json={"todo_ids": todo_ids}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def _complete(client: AsyncClient, headers: dict[str, str], todo_id: int) -> None:
    r = await client.post(f"/v1/todos/{todo_id}/complete", json={}, headers=headers)
    assert r.status_code == 200, r.text


# ---- 인증샷 -----------------------------------------------------------------------


async def test_proof_requires_a_completed_item(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """완료 표시 없이 사진만 올리는 건 인증이 아니다."""
    group = await _group_with(client, auth_headers)
    declaration = await _declare(client, auth_headers, group["group_id"], "운동하기")
    item = declaration["items"][0]

    early = await client.post(
        f"/v1/groups/{group['group_id']}/proofs",
        json={"declaration_item_id": item["declaration_item_id"], "file_key": FILE_KEY},
        headers=auth_headers,
    )
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "TODO_NOT_DONE"

    await _complete(client, auth_headers, item["todo_id"])
    r = await client.post(
        f"/v1/groups/{group['group_id']}/proofs",
        json={
            "declaration_item_id": item["declaration_item_id"],
            "file_key": FILE_KEY,
            "caption": "헬스장 다녀옴",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    post = r.json()["data"]
    assert post["caption"] == "헬스장 다녀옴"
    assert post["file_key"] == FILE_KEY
    assert post["comment_count"] == 0 and post["reactions"] == {}
    # 버킷이 설정되지 않은 환경에서는 null. file_key 는 언제나 함께 내려간다.
    assert post["image_url"] is None


async def test_proof_is_one_per_item_and_owner_only(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    _, jiho = await make_user("지호")
    group = await _group_with(client, auth_headers, jiho)
    declaration = await _declare(client, auth_headers, group["group_id"], "운동하기")
    item = declaration["items"][0]
    await _complete(client, auth_headers, item["todo_id"])

    body = {"declaration_item_id": item["declaration_item_id"], "file_key": FILE_KEY}
    assert (
        await client.post(
            f"/v1/groups/{group['group_id']}/proofs", json=body, headers=auth_headers
        )
    ).status_code == 201

    duplicate = await client.post(
        f"/v1/groups/{group['group_id']}/proofs", json=body, headers=auth_headers
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "PROOF_ALREADY_EXISTS"

    stolen = await client.post(
        f"/v1/groups/{group['group_id']}/proofs", json=body, headers=jiho
    )
    assert stolen.status_code == 403


# ---- 피드 -------------------------------------------------------------------------


async def test_feed_groups_by_user_and_shows_missed(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    """미달성 항목을 API 에서부터 숨기지 않는다 (§6.3)."""
    _, jiho = await make_user("지호")
    group = await _group_with(client, auth_headers, jiho)

    mine = await _declare(client, auth_headers, group["group_id"], "운동하기", "논문 읽기")
    await _complete(client, auth_headers, mine["items"][0]["todo_id"])
    await client.post(
        f"/v1/groups/{group['group_id']}/proofs",
        json={
            "declaration_item_id": mine["items"][0]["declaration_item_id"],
            "file_key": FILE_KEY,
        },
        headers=auth_headers,
    )
    await _declare(client, jiho, group["group_id"], "코딩테스트")

    r = await client.get(f"/v1/groups/{group['group_id']}/feed", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page"]["has_next"] is False
    entries = body["data"]
    assert len(entries) == 2

    me = entries[0]
    assert me["declared_count"] == 2
    assert me["done_count"] == 1
    assert me["achievement_rate"] == 0.5
    assert len(me["posts"]) == 1
    assert me["posts"][0]["user"]["nickname"] == "태한"
    assert me["missed_items"] == [{"title": "논문 읽기", "status": "pending"}]

    other = entries[1]
    assert other["user"]["nickname"] == "지호"
    assert other["posts"] == []
    assert other["missed_items"] == [{"title": "코딩테스트", "status": "pending"}]


async def test_feed_paginates_and_clears_unread(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    _, jiho = await make_user("지호")
    group = await _group_with(client, auth_headers, jiho)

    other = await _declare(client, jiho, group["group_id"], "코딩테스트")
    await _complete(client, jiho, other["items"][0]["todo_id"])
    await client.post(
        f"/v1/groups/{group['group_id']}/proofs",
        json={
            "declaration_item_id": other["items"][0]["declaration_item_id"],
            "file_key": FILE_KEY,
        },
        headers=jiho,
    )
    await _declare(client, auth_headers, group["group_id"], "운동하기")

    listed = (await client.get("/v1/groups", headers=auth_headers)).json()["data"][0]
    assert listed["unread_count"] == 1  # 남이 올린 것만 센다
    assert listed["last_activity_at"] is not None

    first = await client.get(
        f"/v1/groups/{group['group_id']}/feed?limit=1", headers=auth_headers
    )
    page = first.json()
    assert len(page["data"]) == 1
    assert page["page"]["has_next"] is True

    second = await client.get(
        f"/v1/groups/{group['group_id']}/feed?limit=1&cursor={page['page']['next_cursor']}",
        headers=auth_headers,
    )
    assert len(second.json()["data"]) == 1
    assert second.json()["data"][0]["user"]["user_id"] != page["data"][0]["user"]["user_id"]

    after = (await client.get("/v1/groups", headers=auth_headers)).json()["data"][0]
    assert after["unread_count"] == 0  # 피드를 읽으면 배지가 내려간다


async def test_feed_requires_membership(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    group = await _group_with(client, auth_headers)
    _, stranger = await make_user("남")
    r = await client.get(f"/v1/groups/{group['group_id']}/feed", headers=stranger)
    assert r.status_code == 403


# ---- 댓글·리액션 --------------------------------------------------------------------


async def _post_with_proof(client: AsyncClient, owner, *others) -> tuple[dict, int]:
    group = await _group_with(client, owner, *others)
    declaration = await _declare(client, owner, group["group_id"], "운동하기")
    item = declaration["items"][0]
    await _complete(client, owner, item["todo_id"])
    r = await client.post(
        f"/v1/groups/{group['group_id']}/proofs",
        json={"declaration_item_id": item["declaration_item_id"], "file_key": FILE_KEY},
        headers=owner,
    )
    return group, r.json()["data"]["post_id"]


async def test_comments(client: AsyncClient, auth_headers: dict[str, str], make_user) -> None:
    _, jiho = await make_user("지호")
    _, post_id = await _post_with_proof(client, auth_headers, jiho)

    r = await client.post(
        f"/v1/posts/{post_id}/comments", json={"content": "고생했다"}, headers=jiho
    )
    assert r.status_code == 201, r.text
    comment = r.json()["data"]
    assert comment["content"] == "고생했다"
    assert comment["user"]["nickname"] == "지호"

    listed = await client.get(f"/v1/posts/{post_id}/comments", headers=auth_headers)
    assert len(listed.json()["data"]) == 1

    detail = (await client.get(f"/v1/posts/{post_id}", headers=auth_headers)).json()["data"]
    assert detail["comment_count"] == 1

    # 작성자 본인만 지울 수 있다
    forbidden = await client.delete(
        f"/v1/comments/{comment['comment_id']}", headers=auth_headers
    )
    assert forbidden.status_code == 403
    assert (
        await client.delete(f"/v1/comments/{comment['comment_id']}", headers=jiho)
    ).status_code == 204
    assert (await client.get(f"/v1/posts/{post_id}/comments", headers=jiho)).json()["data"] == []


async def test_reaction_is_one_per_user_and_replaces(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    _, jiho = await make_user("지호")
    _, post_id = await _post_with_proof(client, auth_headers, jiho)

    r = await client.put(f"/v1/posts/{post_id}/reactions", json={"type": "fire"}, headers=jiho)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["reactions"] == {"fire": 1}
    assert r.json()["data"]["my_reaction"] == "fire"

    swapped = await client.put(
        f"/v1/posts/{post_id}/reactions", json={"type": "clap"}, headers=jiho
    )
    assert swapped.json()["data"]["reactions"] == {"clap": 1}  # 누적이 아니라 교체

    both = await client.put(
        f"/v1/posts/{post_id}/reactions", json={"type": "fire"}, headers=auth_headers
    )
    assert both.json()["data"]["reactions"] == {"clap": 1, "fire": 1}

    assert (
        await client.delete(f"/v1/posts/{post_id}/reactions", headers=jiho)
    ).status_code == 204
    # 취소는 멱등이다
    assert (
        await client.delete(f"/v1/posts/{post_id}/reactions", headers=jiho)
    ).status_code == 204

    detail = (await client.get(f"/v1/posts/{post_id}", headers=jiho)).json()["data"]
    assert detail["reactions"] == {"fire": 1}
    assert detail["my_reaction"] is None


async def test_reaction_type_is_whitelisted(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _, post_id = await _post_with_proof(client, auth_headers)
    r = await client.put(
        f"/v1/posts/{post_id}/reactions", json={"type": "poop"}, headers=auth_headers
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_outsiders_cannot_read_or_comment(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    _, post_id = await _post_with_proof(client, auth_headers)
    _, stranger = await make_user("남")

    assert (await client.get(f"/v1/posts/{post_id}", headers=stranger)).status_code == 403
    r = await client.post(
        f"/v1/posts/{post_id}/comments", json={"content": "끼어들기"}, headers=stranger
    )
    assert r.status_code == 403


async def test_unknown_post_is_404(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    r = await client.get("/v1/posts/999999", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "POST_NOT_FOUND"


# ---- presign ----------------------------------------------------------------------


async def test_presign_is_503_without_storage_credentials(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """자격증명이 없으면 이 엔드포인트만 막힌다. 서버는 정상이다."""
    r = await client.post(
        "/v1/uploads/presign",
        json={"content_type": "image/jpeg", "purpose": "proof"},
        headers=auth_headers,
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "STORAGE_UNAVAILABLE"


async def test_presign_rejects_unsupported_type(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.post(
        "/v1/uploads/presign",
        json={"content_type": "application/pdf"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
