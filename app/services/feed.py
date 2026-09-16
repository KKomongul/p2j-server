"""인증샷과 피드 (API 명세 §6.3, ERD §3.13).

피드는 스토리형 UI 에 맞춰 **사용자별·날짜별로 묶어서** 내려간다.

`missed_items` 를 반드시 함께 내려보낸다. 아침에 선언해 놓고 못 한 항목이 그대로 보이는
게 이 앱의 차별점이라, API 단에서 숨기지 않는다 (§6.3).

이미지는 서버를 거치지 않는다. 클라이언트가 presign 으로 받은 URL 에 직접 올리고
`file_key` 만 넘긴다 (services/uploads.py).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Forbidden
from app.core.response import clamp_limit, decode_cursor, encode_cursor
from app.core.time import now_utc, to_kst_iso
from app.db.models.declaration import Declaration, DeclarationItem
from app.db.models.group import GroupMember
from app.db.models.proof import Comment, Proof, Reaction
from app.db.models.todo import Todo
from app.db.models.user import User
from app.services import declarations as decl_svc
from app.services.uploads import public_url

# ---- 직렬화 -------------------------------------------------------------------------


def proof_to_dict(
    proof: Proof,
    *,
    author: User | None = None,
    comment_count: int = 0,
    reactions: dict[str, int] | None = None,
    my_reaction: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "post_id": proof.post_id,
        "declaration_item_id": proof.declaration_item_id,
        "group_id": proof.group_id,
        "user_id": proof.user_id,
        "date": proof.date.isoformat(),
        "file_key": proof.file_key,
        "image_url": public_url(proof.file_key),
        "caption": proof.caption,
        "created_at": to_kst_iso(proof.created_at),
        "comment_count": comment_count,
        "reactions": reactions or {},
        "my_reaction": my_reaction,
    }
    if author is not None:
        data["user"] = {
            "user_id": author.user_id,
            "nickname": author.nickname,
            "profile_image_url": author.profile_image_url,
        }
    return data


async def decorate(
    db: AsyncSession, proofs: list[Proof], viewer_id: int
) -> dict[int, dict[str, Any]]:
    """게시물 여러 개의 댓글 수·리액션 집계를 쿼리 두 번으로 가져온다."""
    ids = [p.post_id for p in proofs]
    if not ids:
        return {}

    comment_rows = (
        await db.execute(
            select(Comment.post_id, func.count(Comment.comment_id))
            .where(Comment.post_id.in_(ids), Comment.deleted_at.is_(None))
            .group_by(Comment.post_id)
        )
    ).all()
    counts = {int(pid): int(n) for pid, n in comment_rows}

    reaction_rows = (
        await db.execute(
            select(Reaction.post_id, Reaction.type, Reaction.user_id).where(
                Reaction.post_id.in_(ids)
            )
        )
    ).all()
    tally: dict[int, dict[str, int]] = {}
    mine: dict[int, str] = {}
    for post_id, kind, user_id in reaction_rows:
        bucket = tally.setdefault(int(post_id), {})
        bucket[kind] = bucket.get(kind, 0) + 1
        if int(user_id) == viewer_id:
            mine[int(post_id)] = kind

    return {
        pid: {
            "comment_count": counts.get(pid, 0),
            "reactions": tally.get(pid, {}),
            "my_reaction": mine.get(pid),
        }
        for pid in ids
    }


# ---- 인증샷 등록 --------------------------------------------------------------------


async def create_proof(
    db: AsyncSession,
    user: User,
    group_id: int,
    declaration_item_id: int,
    file_key: str,
    caption: str | None,
) -> Proof:
    """선언한 항목에 인증샷을 붙인다 (§6.3).

    끝낸 항목에만 붙일 수 있다. 완료 표시 없이 사진만 올리는 건 인증이 아니다.
    """
    item = await db.get(DeclarationItem, declaration_item_id)
    if item is None:
        raise AppError("DECLARATION_NOT_FOUND", "선언 항목을 찾을 수 없어요.")

    declaration = await db.get(Declaration, item.declaration_id)
    if declaration is None or declaration.group_id != group_id:
        raise AppError("DECLARATION_NOT_FOUND", "선언 항목을 찾을 수 없어요.")
    if declaration.user_id != user.user_id:
        raise Forbidden()  # 남의 선언에는 못 올린다

    todo = await db.get(Todo, item.todo_id) if item.todo_id else None
    if decl_svc.item_status(item, todo, declaration.date) != "done":
        raise AppError("TODO_NOT_DONE")

    existing = await db.scalar(
        select(Proof.post_id).where(
            Proof.declaration_item_id == declaration_item_id, Proof.deleted_at.is_(None)
        )
    )
    if existing is not None:
        raise AppError("PROOF_ALREADY_EXISTS")

    proof = Proof(
        declaration_item_id=declaration_item_id,
        group_id=group_id,
        user_id=user.user_id,
        date=declaration.date,
        file_key=file_key,
        caption=caption,
    )
    db.add(proof)
    await db.flush()
    await db.refresh(proof)
    return proof


async def delete_proof(db: AsyncSession, user: User, post_id: int) -> None:
    proof = await db.scalar(
        select(Proof).where(Proof.post_id == post_id, Proof.deleted_at.is_(None))
    )
    if proof is None:
        raise AppError("POST_NOT_FOUND")
    if proof.user_id != user.user_id:
        raise Forbidden()
    proof.deleted_at = now_utc()
    await db.flush()


# ---- 피드 -------------------------------------------------------------------------


async def list_feed(
    db: AsyncSession,
    viewer: User,
    group_id: int,
    day: date | None,
    cursor: str | None,
    limit: int | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """날짜 내림차순, 같은 날 안에서는 가입 순. 커서는 (date, user_id)."""
    size = clamp_limit(limit)

    stmt = select(Declaration).where(Declaration.group_id == group_id)
    if day is not None:
        stmt = stmt.where(Declaration.date == day)

    decoded = decode_cursor(cursor)
    if decoded and decoded.get("date") and isinstance(decoded.get("user_id"), int):
        edge = date.fromisoformat(str(decoded["date"]))
        stmt = stmt.where(
            (Declaration.date < edge)
            | ((Declaration.date == edge) & (Declaration.user_id > decoded["user_id"]))
        )

    rows = list(
        await db.scalars(
            stmt.order_by(Declaration.date.desc(), Declaration.user_id).limit(size + 1)
        )
    )

    next_cursor = None
    if len(rows) > size:
        rows = rows[:size]
        last = rows[-1]
        next_cursor = encode_cursor({"date": last.date.isoformat(), "user_id": last.user_id})

    return await _build_entries(db, viewer, rows), next_cursor


async def _build_entries(
    db: AsyncSession, viewer: User, declarations: list[Declaration]
) -> list[dict[str, Any]]:
    if not declarations:
        return []

    authors = {
        u.user_id: u
        for u in await db.scalars(
            select(User).where(User.user_id.in_({d.user_id for d in declarations}))
        )
    }
    todos, proofs_by_item = await decl_svc.items_context(db, declarations)
    all_proofs = list(proofs_by_item.values())
    extras = await decorate(db, all_proofs, viewer.user_id)

    entries = []
    for declaration in declarations:
        author = authors.get(declaration.user_id)
        posts: list[dict[str, Any]] = []
        missed: list[dict[str, Any]] = []
        done = 0

        for item in declaration.items:
            todo = todos.get(item.todo_id) if item.todo_id else None
            status = decl_svc.item_status(item, todo, declaration.date)
            if status == "done":
                done += 1
            else:
                # 못 한 것을 그대로 보여준다. 이걸 빼면 기능의 절반이 사라진다.
                missed.append({"title": item.title_snapshot, "status": status})

            proof = proofs_by_item.get(item.declaration_item_id)
            if proof is not None:
                posts.append(
                    proof_to_dict(proof, author=author, **extras.get(proof.post_id, {}))
                )

        entries.append(
            {
                "user": {
                    "user_id": author.user_id if author else declaration.user_id,
                    "nickname": author.nickname if author else "",
                    "profile_image_url": author.profile_image_url if author else None,
                },
                "date": declaration.date.isoformat(),
                "declaration_id": declaration.declaration_id,
                "declared_count": len(declaration.items),
                "done_count": done,
                "achievement_rate": round(done / len(declaration.items), 2)
                if declaration.items
                else 0.0,
                "posts": posts,
                "missed_items": missed,
            }
        )
    return entries


# ---- 단건 조회 ---------------------------------------------------------------------


async def get_visible_proof(db: AsyncSession, user: User, post_id: int) -> Proof:
    """게시물 + 열람 권한 확인. 같은 그룹 구성원만 볼 수 있다."""
    proof = await db.scalar(
        select(Proof).where(Proof.post_id == post_id, Proof.deleted_at.is_(None))
    )
    if proof is None:
        raise AppError("POST_NOT_FOUND")

    member = await db.scalar(
        select(GroupMember.id).where(
            GroupMember.group_id == proof.group_id,
            GroupMember.user_id == user.user_id,
            GroupMember.left_at.is_(None),
        )
    )
    if member is None:
        raise AppError("NOT_GROUP_MEMBER")
    return proof
