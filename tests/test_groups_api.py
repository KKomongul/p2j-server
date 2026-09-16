"""/groups/* — 그룹과 선언 (명세 §6.1~§6.2)."""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now_utc, service_today
from app.db.models import GroupInvite, User


async def _create_group(client: AsyncClient, headers: dict[str, str], **overrides) -> dict:
    payload = {"name": "3학년 개발조", **overrides}
    r = await client.post("/v1/groups", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def _add_todo(client: AsyncClient, headers: dict[str, str], title: str) -> int:
    r = await client.post("/v1/todos", json={"title": title}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]["todo_id"]


# ---- 생성·조회 ---------------------------------------------------------------------


async def test_create_group_defaults(
    client: AsyncClient, auth_headers: dict[str, str], user: User
) -> None:
    group = await _create_group(client, auth_headers)
    assert group["name"] == "3학년 개발조"
    assert group["owner_id"] == user.user_id
    assert group["member_count"] == 1
    assert group["max_members"] == 6
    assert group["my_role"] == "owner"
    assert group["group_streak"] == 0
    assert group["invite_code"].startswith("P2J-")


async def test_max_members_is_capped_at_six(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """신청서 기준 3~6명. 서버가 강제한다 (§6.1)."""
    for bad in (2, 7):
        r = await client.post(
            "/v1/groups", json={"name": "x", "max_members": bad}, headers=auth_headers
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_list_my_groups_has_activity_fields(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await _create_group(client, auth_headers)
    r = await client.get("/v1/groups", headers=auth_headers)
    assert r.status_code == 200
    entry = r.json()["data"][0]
    assert entry["unread_count"] == 0
    assert entry["last_activity_at"] is None


async def test_non_member_gets_403_not_404(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    """그룹 ID 는 초대 코드로 공유되는 값이라 존재를 숨기지 않는다."""
    group = await _create_group(client, auth_headers)
    _, stranger = await make_user("남")

    r = await client.get(f"/v1/groups/{group['group_id']}", headers=stranger)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "NOT_GROUP_MEMBER"

    missing = await client.get("/v1/groups/999999", headers=auth_headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "GROUP_NOT_FOUND"


# ---- 초대·참여 ---------------------------------------------------------------------


async def test_join_with_invite_code(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    group = await _create_group(client, auth_headers)
    _, jiho = await make_user("지호")

    r = await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=jiho
    )
    assert r.status_code == 200, r.text
    joined = r.json()["data"]
    assert joined["group_id"] == group["group_id"]
    assert joined["member_count"] == 2
    assert joined["my_role"] == "member"

    again = await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=jiho
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ALREADY_MEMBER"


async def test_join_rejects_unknown_and_expired_codes(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, make_user
) -> None:
    group = await _create_group(client, auth_headers)
    _, jiho = await make_user("지호")

    bad = await client.post("/v1/groups/join", json={"invite_code": "P2J-ZZZZ"}, headers=jiho)
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "INVALID_INVITE_CODE"

    invite = await db.scalar(
        GroupInvite.__table__.select().where(GroupInvite.code == group["invite_code"])
    )
    await db.execute(
        GroupInvite.__table__.update()
        .where(GroupInvite.code == group["invite_code"])
        .values(expires_at=now_utc() - timedelta(days=1))
    )
    await db.commit()
    assert invite is not None

    expired = await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=jiho
    )
    assert expired.status_code == 422
    assert expired.json()["error"]["code"] == "INVITE_CODE_EXPIRED"


async def test_group_full(client: AsyncClient, auth_headers: dict[str, str], make_user) -> None:
    group = await _create_group(client, auth_headers, max_members=3)
    for name in ("지호", "영준"):
        _, headers = await make_user(name)
        r = await client.post(
            "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=headers
        )
        assert r.status_code == 200

    _, late = await make_user("늦은사람")
    r = await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=late
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "GROUP_FULL"


async def test_reissuing_invite_revokes_the_previous_code(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    group = await _create_group(client, auth_headers)
    r = await client.post(f"/v1/groups/{group['group_id']}/invite", headers=auth_headers)
    assert r.status_code == 200
    fresh = r.json()["data"]
    assert fresh["invite_code"] != group["invite_code"]
    assert fresh["expires_at"] is not None

    _, jiho = await make_user("지호")
    old = await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=jiho
    )
    assert old.status_code == 422  # 이전 코드는 폐기됐다

    new = await client.post(
        "/v1/groups/join", json={"invite_code": fresh["invite_code"]}, headers=jiho
    )
    assert new.status_code == 200


# ---- 탈퇴·권한 ---------------------------------------------------------------------


async def test_owner_must_transfer_before_leaving(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    group = await _create_group(client, auth_headers)
    jiho, jiho_headers = await make_user("지호")
    await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=jiho_headers
    )

    blocked = await client.delete(
        f"/v1/groups/{group['group_id']}/members/me", headers=auth_headers
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "ADMIN_MUST_TRANSFER"

    handover = await client.post(
        f"/v1/groups/{group['group_id']}/owner",
        json={"user_id": jiho.user_id},
        headers=auth_headers,
    )
    assert handover.status_code == 200
    assert handover.json()["data"]["owner_id"] == jiho.user_id

    left = await client.delete(f"/v1/groups/{group['group_id']}/members/me", headers=auth_headers)
    assert left.status_code == 204


async def test_last_member_can_leave_and_group_closes(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    group = await _create_group(client, auth_headers)
    r = await client.delete(f"/v1/groups/{group['group_id']}/members/me", headers=auth_headers)
    assert r.status_code == 204
    assert (await client.get("/v1/groups", headers=auth_headers)).json()["data"] == []
    gone = await client.get(f"/v1/groups/{group['group_id']}", headers=auth_headers)
    assert gone.status_code == 404


async def test_rejoin_after_leaving_is_allowed(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    """부분 유니크 인덱스 덕에 재가입은 되고 중복 가입은 막힌다 (ERD §3.9)."""
    group = await _create_group(client, auth_headers)
    _, jiho = await make_user("지호")
    await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=jiho
    )
    await client.delete(f"/v1/groups/{group['group_id']}/members/me", headers=jiho)

    again = await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=jiho
    )
    assert again.status_code == 200
    assert again.json()["data"]["member_count"] == 2


# ---- 구성원 현황 --------------------------------------------------------------------


async def test_members_show_today_status(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    group = await _create_group(client, auth_headers)
    _, jiho = await make_user("지호")
    await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=jiho
    )

    todo_id = await _add_todo(client, auth_headers, "운동하기")
    await client.post(f"/v1/todos/{todo_id}/complete", json={}, headers=auth_headers)
    await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"todo_ids": [todo_id]},
        headers=auth_headers,
    )

    r = await client.get(f"/v1/groups/{group['group_id']}/members", headers=auth_headers)
    assert r.status_code == 200, r.text
    members = r.json()["data"]
    assert len(members) == 2

    me = next(m for m in members if m["role"] == "owner")
    assert me["today_declared"] is True
    assert me["today_achievement_rate"] == 1.0
    assert me["streak"] == 1  # 오늘 할 일을 다 끝냈으면 오늘부터 센다

    other = next(m for m in members if m["role"] == "member")
    assert other["today_declared"] is False
    assert other["today_achievement_rate"] == 0.0


# ---- 선언 -------------------------------------------------------------------------


async def test_declaration_locks_todos(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    group = await _create_group(client, auth_headers)
    first = await _add_todo(client, auth_headers, "운동하기")
    second = await _add_todo(client, auth_headers, "논문 읽기")

    r = await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"todo_ids": [first, second]},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    declaration = r.json()["data"]
    assert declaration["date"] == service_today().isoformat()
    assert declaration["locked_at"] is not None
    assert [i["title"] for i in declaration["items"]] == ["운동하기", "논문 읽기"]
    assert all(i["status"] == "pending" and i["proof"] is None for i in declaration["items"])

    # 선언한 항목은 제목·삭제가 잠긴다. 완료 체크는 된다 (04-backend §5.3).
    locked = await client.patch(
        f"/v1/todos/{first}", json={"title": "몰래 바꾸기"}, headers=auth_headers
    )
    assert locked.status_code == 422
    assert locked.json()["error"]["code"] == "DECLARED_TODO_LOCKED"
    assert (await client.delete(f"/v1/todos/{first}", headers=auth_headers)).status_code == 422
    done = await client.post(f"/v1/todos/{first}/complete", json={}, headers=auth_headers)
    assert done.status_code == 200


async def test_declaring_twice_a_day_is_409(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    group = await _create_group(client, auth_headers)
    todo_id = await _add_todo(client, auth_headers, "운동하기")
    body = {"todo_ids": [todo_id]}

    first = await client.post(
        f"/v1/groups/{group['group_id']}/declarations", json=body, headers=auth_headers
    )
    assert first.status_code == 201

    second = await client.post(
        f"/v1/groups/{group['group_id']}/declarations", json=body, headers=auth_headers
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DECLARATION_ALREADY_EXISTS"


async def test_declaration_rejects_bad_input(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    group = await _create_group(client, auth_headers)
    today = service_today()

    empty = await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"todo_ids": []},
        headers=auth_headers,
    )
    assert empty.status_code == 400  # Pydantic min_length

    past = await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"date": (today - timedelta(days=1)).isoformat(), "todo_ids": [1]},
        headers=auth_headers,
    )
    assert past.status_code == 422
    assert past.json()["error"]["code"] == "DECLARATION_CLOSED"

    future = await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"date": (today + timedelta(days=1)).isoformat(), "todo_ids": [1]},
        headers=auth_headers,
    )
    assert future.status_code == 400

    # 남의 할 일은 선언할 수 없다
    other, other_headers = await make_user("지호")
    stolen = await _add_todo(client, other_headers, "남의 할 일")
    assert other.nickname == "지호"
    r = await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"todo_ids": [stolen]},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "todo_ids[0]" in r.json()["error"]["details"]


async def test_declaration_list_includes_undeclared_members(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    """미선언 구성원이 보이는 것 자체가 압력 장치다 (§6.2)."""
    group = await _create_group(client, auth_headers)
    _, jiho = await make_user("지호")
    await client.post(
        "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=jiho
    )

    todo_id = await _add_todo(client, auth_headers, "운동하기")
    await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"todo_ids": [todo_id]},
        headers=auth_headers,
    )

    r = await client.get(
        f"/v1/groups/{group['group_id']}/declarations", headers=auth_headers
    )
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 2
    assert rows[0]["declaration_id"] is not None
    assert rows[1]["declaration_id"] is None  # 아직 선언 안 한 사람
    assert rows[1]["items"] == []


async def test_postponing_a_declared_todo_shows_as_deferred(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """미뤄서 선언에서 빠져나가도 약속한 기록은 남는다 (§6.3)."""
    group = await _create_group(client, auth_headers)
    todo_id = await _add_todo(client, auth_headers, "논문 읽기")
    await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"todo_ids": [todo_id]},
        headers=auth_headers,
    )
    await client.post(
        f"/v1/todos/{todo_id}/postpone",
        json={"to_date": (service_today() + timedelta(days=1)).isoformat()},
        headers=auth_headers,
    )

    rows = (
        await client.get(f"/v1/groups/{group['group_id']}/declarations", headers=auth_headers)
    ).json()["data"]
    assert rows[0]["items"][0]["status"] == "deferred"
    assert rows[0]["items"][0]["title"] == "논문 읽기"
    assert rows[0]["achievement_rate"] == 0.0


async def test_todos_day_view_carries_declaration(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    group = await _create_group(client, auth_headers)
    todo_id = await _add_todo(client, auth_headers, "운동하기")

    before = (await client.get("/v1/todos", headers=auth_headers)).json()["data"]
    assert before["declaration"] is None

    await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"todo_ids": [todo_id]},
        headers=auth_headers,
    )
    after = (await client.get("/v1/todos", headers=auth_headers)).json()["data"]
    assert after["declaration"]["declared_count"] == 1
    assert after["declaration"]["groups"][0]["group_id"] == group["group_id"]
    assert after["items"][0]["is_declared"] is True


async def test_groups_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/v1/groups")).status_code == 401
    assert (await client.post("/v1/groups", json={"name": "x"})).status_code == 401
async def test_todo_carries_declaration_links_and_proof(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """오늘 화면이 인증샷을 어디로 보낼지 알려면 할 일에 선언 항목이 실려야 한다."""
    group = await _create_group(client, auth_headers)
    todo_id = await _add_todo(client, auth_headers, "3km 달리기")

    before = (await client.get("/v1/todos", headers=auth_headers)).json()["data"]
    assert before["items"][0]["declarations"] == []
    assert before["items"][0]["proof"] is None

    await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"todo_ids": [todo_id]},
        headers=auth_headers,
    )

    after = (await client.get("/v1/todos", headers=auth_headers)).json()["data"]
    link = after["items"][0]["declarations"][0]
    assert link["group_id"] == group["group_id"]
    assert link["group_name"] == "3학년 개발조"
    assert link["has_proof"] is False
    assert link["declaration_item_id"] > 0

    await client.post(f"/v1/todos/{todo_id}/complete", json={}, headers=auth_headers)
    await client.post(
        f"/v1/groups/{group['group_id']}/proofs",
        json={
            "declaration_item_id": link["declaration_item_id"],
            "file_key": "proofs/2026/09/16/run.jpg",
            "caption": "3km 완주",
        },
        headers=auth_headers,
    )

    proven = (await client.get("/v1/todos", headers=auth_headers)).json()["data"]
    item = proven["items"][0]
    assert item["proof"]["caption"] == "3km 완주"
    assert item["declarations"][0]["has_proof"] is True


async def test_todo_declared_to_two_groups_lists_both(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """한 할 일을 두 그룹에 걸면 사진 한 장이 양쪽에 붙어야 한다."""
    first = await _create_group(client, auth_headers, name="러닝조")
    second = await _create_group(client, auth_headers, name="개발조")
    todo_id = await _add_todo(client, auth_headers, "3km 달리기")

    for group in (first, second):
        r = await client.post(
            f"/v1/groups/{group['group_id']}/declarations",
            json={"todo_ids": [todo_id]},
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text

    data = (await client.get("/v1/todos", headers=auth_headers)).json()["data"]
    links = data["items"][0]["declarations"]
    assert len(links) == 2
    assert {link["group_name"] for link in links} == {"러닝조", "개발조"}
    # 선언 항목 id 는 그룹마다 다르다. 사진은 각각에 따로 붙는다.
    assert len({link["declaration_item_id"] for link in links}) == 2
async def test_member_rate_matches_declaration_status(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """미뤘다가 다음 날 끝낸 항목이 오늘 달성으로 잡히면 안 된다.

    구성원 목록과 선언 현황이 다른 숫자를 말하던 회귀. 완료 판정이 두 벌이었다.
    """
    group = await _create_group(client, auth_headers)
    todo_id = await _add_todo(client, auth_headers, "논문 읽기")
    await client.post(
        f"/v1/groups/{group['group_id']}/declarations",
        json={"todo_ids": [todo_id]},
        headers=auth_headers,
    )

    # 선언한 뒤 내일로 미루고 거기서 끝낸다.
    tomorrow = (service_today() + timedelta(days=1)).isoformat()
    moved = await client.post(
        f"/v1/todos/{todo_id}/postpone",
        json={"to_date": tomorrow},
        headers=auth_headers,
    )
    assert moved.status_code == 200, moved.text
    await client.post(f"/v1/todos/{todo_id}/complete", json={}, headers=auth_headers)

    gid = group["group_id"]
    members = (await client.get(f"/v1/groups/{gid}/members", headers=auth_headers)).json()
    rows = (await client.get(f"/v1/groups/{gid}/declarations", headers=auth_headers)).json()
    ranking = (await client.get(f"/v1/groups/{gid}/ranking", headers=auth_headers)).json()

    # 셋 다 "오늘은 못 했다" 로 말해야 한다.
    assert rows["data"][0]["items"][0]["status"] == "deferred"
    assert rows["data"][0]["achievement_rate"] == 0.0
    assert members["data"][0]["today_achievement_rate"] == 0.0
    assert ranking["data"]["rankings"][0]["achievement_rate"] == 0.0
