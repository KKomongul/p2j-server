"""그룹 (API 명세 §6.1, ERD §3.8~§3.10).

신청서 기준 3~6명. 상한은 서버가 강제한다.

권한은 두 단계뿐이다.

- **구성원** — 조회·선언·인증·초대코드 발급. 6명짜리 스터디에 결재 단계를 두면 안 쓴다.
- **방장** — 마지막 한 명이 아닌 이상 그냥 나갈 수 없다. 먼저 넘겨야 한다.

탈퇴는 `left_at` 을 채우는 soft delete 다. 부분 유니크 인덱스 덕에 재가입은 되고
중복 가입은 DB 가 막는다 (ERD §3.9).
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.time import as_utc, now_utc, service_today, to_kst_iso
from app.db.models.daily_stat import UserDailyStat
from app.db.models.declaration import Declaration, DeclarationItem
from app.db.models.group import (
    INVITE_TTL_DAYS,
    Group,
    GroupInvite,
    GroupMember,
)
from app.db.models.proof import Proof
from app.db.models.todo import Todo
from app.db.models.user import User
from app.services.stats import STREAK_SCAN_DAYS, streak_from_rows

# 0/O, 1/I/L 처럼 눈으로 헷갈리는 글자는 뺐다. 초대 코드는 카톡으로 옮겨 적는 값이다.
INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_PREFIX = "P2J-"
INVITE_BODY_LENGTH = 4
INVITE_MAX_ATTEMPTS = 8


def _new_code() -> str:
    body = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_BODY_LENGTH))
    return f"{INVITE_PREFIX}{body}"


# ---- 접근 제어 ---------------------------------------------------------------------


async def get_group(db: AsyncSession, group_id: int) -> Group:
    group = await db.scalar(
        select(Group).where(Group.group_id == group_id, Group.deleted_at.is_(None))
    )
    if group is None:
        raise AppError("GROUP_NOT_FOUND")
    return group


async def require_member(db: AsyncSession, user: User, group_id: int) -> tuple[Group, GroupMember]:
    """구성원이 아니면 403. 그룹 자체가 없으면 404.

    "없는 그룹" 과 "남의 그룹" 을 구분해 준다. todo/goal 과 달리 그룹 ID 는 초대 코드로
    공유되는 값이라 존재 자체를 숨길 이유가 없다.
    """
    group = await get_group(db, group_id)
    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user.user_id,
            GroupMember.left_at.is_(None),
        )
    )
    if member is None:
        raise AppError("NOT_GROUP_MEMBER")
    return group, member


async def _member_count(db: AsyncSession, group_id: int) -> int:
    count = await db.scalar(
        select(func.count(GroupMember.id)).where(
            GroupMember.group_id == group_id, GroupMember.left_at.is_(None)
        )
    )
    return int(count or 0)


# ---- 초대 코드 ---------------------------------------------------------------------


async def _active_invite(db: AsyncSession, group_id: int) -> GroupInvite | None:
    """폐기되지 않은 최신 코드. 만료 판정은 파이썬에서 한다.

    시각 비교를 SQL 에 맡기면 SQLite(테스트)와 PostgreSQL 의 tz 처리가 갈린다.
    auth.py 의 refresh token 만료 처리와 같은 방식이다.
    """
    invite = await db.scalar(
        select(GroupInvite)
        .where(GroupInvite.group_id == group_id, GroupInvite.revoked_at.is_(None))
        .order_by(GroupInvite.invite_id.desc())
    )
    if invite is None or as_utc(invite.expires_at) <= now_utc():
        return None
    return invite


async def issue_invite(db: AsyncSession, group_id: int, created_by: int) -> GroupInvite:
    """새 코드를 만든다. 이전 코드는 폐기한다 — 유효한 코드는 그룹당 하나뿐이다."""
    previous = await _active_invite(db, group_id)
    if previous is not None:
        previous.revoked_at = now_utc()

    for _ in range(INVITE_MAX_ATTEMPTS):
        code = _new_code()
        taken = await db.scalar(select(GroupInvite.invite_id).where(GroupInvite.code == code))
        if taken is None:
            break
    else:  # 31^4 ≈ 92만 조합. 여기까지 왔다면 코드 공간이 아니라 다른 게 잘못된 것이다.
        raise AppError("INTERNAL_ERROR")

    invite = GroupInvite(
        group_id=group_id,
        code=code,
        created_by=created_by,
        expires_at=now_utc() + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    await db.flush()
    await db.refresh(invite)
    return invite


def invite_to_dict(invite: GroupInvite) -> dict[str, Any]:
    return {"invite_code": invite.code, "expires_at": to_kst_iso(invite.expires_at)}


# ---- 직렬화 -------------------------------------------------------------------------


async def group_to_dict(
    db: AsyncSession,
    group: Group,
    member: GroupMember | None = None,
    *,
    with_activity: bool = False,
) -> dict[str, Any]:
    invite = await _active_invite(db, group.group_id)
    data: dict[str, Any] = {
        "group_id": group.group_id,
        "name": group.name,
        "owner_id": group.owner_id,
        "member_count": await _member_count(db, group.group_id),
        "max_members": group.max_members,
        "invite_code": invite.code if invite else None,
        "group_streak": group.group_streak,
        "last_streak_date": group.last_streak_date.isoformat()
        if group.last_streak_date
        else None,
        "my_role": member.role if member else None,
        "created_at": to_kst_iso(group.created_at),
    }
    if with_activity:
        data.update(await _activity(db, group.group_id, member))
    return data


async def _activity(
    db: AsyncSession, group_id: int, member: GroupMember | None
) -> dict[str, Any]:
    """목록 화면의 미확인 배지 (§6.1). 카톡 방 목록과 같은 모양을 만든다."""
    last_activity = await db.scalar(
        select(func.max(Proof.created_at)).where(
            Proof.group_id == group_id, Proof.deleted_at.is_(None)
        )
    )
    stmt = select(func.count(Proof.post_id)).where(
        Proof.group_id == group_id, Proof.deleted_at.is_(None)
    )
    if member is not None:
        # 내가 올린 건 미확인이 아니다.
        stmt = stmt.where(Proof.user_id != member.user_id)
        if member.last_read_at is not None:
            stmt = stmt.where(Proof.created_at > member.last_read_at)
    unread = await db.scalar(stmt)
    return {
        "unread_count": int(unread or 0),
        "last_activity_at": to_kst_iso(last_activity),
    }


# ---- 생성·참여·탈퇴 ------------------------------------------------------------------


async def create_group(db: AsyncSession, user: User, name: str, max_members: int) -> Group:
    group = Group(name=name, owner_id=user.user_id, max_members=max_members)
    db.add(group)
    await db.flush()

    db.add(
        GroupMember(
            group_id=group.group_id,
            user_id=user.user_id,
            role="owner",
            joined_at=now_utc(),
            # last_read_at 은 NULL 로 둔다. 가입 시각을 "읽음" 으로 치면 들어오기 직전에
            # 올라온 인증샷이 배지 없이 묻힌다. 실제로 피드를 열 때 채운다.
        )
    )
    await issue_invite(db, group.group_id, user.user_id)
    await db.flush()
    await db.refresh(group)
    return group


async def list_my_groups(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Group, GroupMember)
            .join(GroupMember, GroupMember.group_id == Group.group_id)
            .where(
                GroupMember.user_id == user.user_id,
                GroupMember.left_at.is_(None),
                Group.deleted_at.is_(None),
            )
            .order_by(Group.group_id.desc())
        )
    ).all()
    return [
        await group_to_dict(db, group, member, with_activity=True) for group, member in rows
    ]


async def join_group(db: AsyncSession, user: User, invite_code: str) -> Group:
    code = invite_code.strip().upper()
    invite = await db.scalar(
        select(GroupInvite).where(GroupInvite.code == code, GroupInvite.revoked_at.is_(None))
    )
    if invite is None:
        raise AppError("INVALID_INVITE_CODE")
    if as_utc(invite.expires_at) <= now_utc():
        raise AppError("INVITE_CODE_EXPIRED")

    group = await get_group(db, invite.group_id)

    existing = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group.group_id,
            GroupMember.user_id == user.user_id,
            GroupMember.left_at.is_(None),
        )
    )
    if existing is not None:
        raise AppError("ALREADY_MEMBER")

    if await _member_count(db, group.group_id) >= group.max_members:
        raise AppError("GROUP_FULL")

    db.add(
        GroupMember(
            group_id=group.group_id,
            user_id=user.user_id,
            role="member",
            joined_at=now_utc(),
        )
    )
    await db.flush()
    return group


async def leave_group(db: AsyncSession, user: User, group_id: int) -> None:
    group, member = await require_member(db, user, group_id)

    if member.role == "owner" and await _member_count(db, group_id) > 1:
        # 방장이 그냥 나가면 초대·정리할 사람이 없는 그룹이 남는다.
        raise AppError("ADMIN_MUST_TRANSFER")

    member.left_at = now_utc()
    if await _member_count(db, group_id) == 0:
        group.deleted_at = now_utc()  # 마지막 사람이 나가면 그룹도 닫는다
    await db.flush()


async def transfer_ownership(
    db: AsyncSession, user: User, group_id: int, to_user_id: int
) -> Group:
    """방장 넘기기. 나가기 전에 필요하다 (ADMIN_MUST_TRANSFER)."""
    group, member = await require_member(db, user, group_id)
    if member.role != "owner":
        raise AppError("NOT_GROUP_ADMIN")
    if to_user_id == user.user_id:
        raise AppError("CANNOT_KICK_SELF", "이미 방장이에요.")

    target = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == to_user_id,
            GroupMember.left_at.is_(None),
        )
    )
    if target is None:
        raise AppError("USER_NOT_FOUND", "그룹에 없는 사용자예요.")

    member.role = "member"
    target.role = "owner"
    group.owner_id = to_user_id
    await db.flush()
    await db.refresh(group)
    return group


# ---- 구성원 목록 ---------------------------------------------------------------------


async def list_members(db: AsyncSession, group_id: int, day: date | None = None) -> list[dict]:
    """구성원과 오늘 현황 (§6.1).

    `today_achievement_rate` 는 **선언한 항목 기준**이다. 선언하지 않았으면 0.0.
    개인 할 일 전체 달성률을 쓰면 "그룹에 약속한 것" 과 "혼자 한 것" 이 섞여
    바로 옆 랭킹 숫자와 어긋난다.
    """
    target = day or service_today()

    rows = (
        await db.execute(
            select(GroupMember, User)
            .join(User, User.user_id == GroupMember.user_id)
            .where(GroupMember.group_id == group_id, GroupMember.left_at.is_(None))
            .order_by(GroupMember.joined_at, GroupMember.id)
        )
    ).all()
    user_ids = [m.user_id for m, _ in rows]
    if not user_ids:
        return []

    rates = await declared_rates(db, group_id, user_ids, target)
    streaks = await member_streaks(db, user_ids, target)

    out = []
    for member, account in rows:
        declared = member.user_id in rates
        out.append(
            {
                "user_id": account.user_id,
                "nickname": account.nickname,
                "profile_image_url": account.profile_image_url,
                "role": member.role,
                "joined_at": to_kst_iso(member.joined_at),
                "today_declared": declared,
                "today_achievement_rate": rates.get(member.user_id, 0.0),
                "streak": streaks.get(member.user_id, 0),
            }
        )
    return out


async def declared_rates(
    db: AsyncSession, group_id: int, user_ids: list[int], day: date
) -> dict[int, float]:
    """선언한 사람별 {user_id: 달성률}. 선언하지 않은 사람은 키 자체가 없다."""
    rows = (
        await db.execute(
            select(
                Declaration.user_id,
                func.count(DeclarationItem.declaration_item_id),
                func.count(DeclarationItem.declaration_item_id).filter(Todo.status == "done"),
            )
            .join(
                DeclarationItem,
                DeclarationItem.declaration_id == Declaration.declaration_id,
            )
            .outerjoin(Todo, Todo.todo_id == DeclarationItem.todo_id)
            .where(
                Declaration.group_id == group_id,
                Declaration.date == day,
                Declaration.user_id.in_(user_ids),
            )
            .group_by(Declaration.user_id)
        )
    ).all()
    return {
        int(user_id): round(int(done) / int(total), 2) if int(total) else 0.0
        for user_id, total, done in rows
    }


async def member_streaks(db: AsyncSession, user_ids: list[int], day: date) -> dict[int, int]:
    """구성원 전원의 개인 연속 기록을 쿼리 한 번으로. 6명 × 90일이라 파이썬에서 센다."""
    rows = (
        await db.execute(
            select(UserDailyStat.user_id, UserDailyStat.date).where(
                UserDailyStat.user_id.in_(user_ids),
                UserDailyStat.date >= day - timedelta(days=STREAK_SCAN_DAYS),
                UserDailyStat.date <= day,
                UserDailyStat.total_count > 0,
                UserDailyStat.done_count >= UserDailyStat.total_count,
            )
        )
    ).all()
    cleared: dict[int, set[date]] = {}
    for user_id, value in rows:
        cleared.setdefault(int(user_id), set()).add(value)
    return {uid: streak_from_rows(cleared.get(uid, set()), day) for uid in user_ids}


async def touch_read(db: AsyncSession, member: GroupMember) -> None:
    """피드를 읽으면 미확인 배지를 0 으로. 별도 엔드포인트를 만들지 않는다.

    `proofs.created_at` 이 DB 시각(`now()`)이므로 여기도 DB 시각으로 찍는다.
    앱 시계와 DB 시계를 섞어 비교하면 배지가 하나씩 어긋난다.
    """
    member.last_read_at = func.now()
    await db.flush()
    await db.refresh(member)
