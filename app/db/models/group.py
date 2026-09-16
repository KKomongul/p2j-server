"""groups · group_members · group_invites (ERD v1 §3.8~§3.10).

신청서 기준 그룹은 3~6명이다. 상한은 서버가 강제한다 (§6.1).
ENUM 대신 varchar + CHECK — goal/todo 와 같은 방식 (ERD §5 의 선택지).
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin

MEMBER_ROLES = ("owner", "member")
MIN_MEMBERS = 3
MAX_MEMBERS = 6
INVITE_TTL_DAYS = 7


class Group(TimestampMixin, Base):
    __tablename__ = "groups"
    __table_args__ = (
        CheckConstraint("max_members BETWEEN 3 AND 6", name="max_members"),
        CheckConstraint("group_streak >= 0", name="group_streak"),
    )

    group_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    max_members: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=MAX_MEMBERS, server_default=str(MAX_MEMBERS)
    )
    # 배치(04:10)가 전일을 판정해 올린다. 선언한 멤버 전원이 100% 끝낸 날만 +1 (§6.5).
    group_streak: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    last_streak_date: Mapped[date_type | None] = mapped_column(Date)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupMember(TimestampMixin, Base):
    """탈퇴는 soft delete(`left_at`). 부분 유니크 인덱스라 재가입은 되고 중복 가입은 막힌다."""

    __tablename__ = "group_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'member')", name="role"),
        Index(
            "uq_group_members_group_id_user_id_active",
            "group_id",
            "user_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
            sqlite_where=text("left_at IS NULL"),
        ),
        Index(
            "ix_group_members_user_id_active",
            "user_id",
            postgresql_where=text("left_at IS NULL"),
            sqlite_where=text("left_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("groups.group_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 피드 미확인 배지용. 이 시각 이후에 올라온 인증샷 수가 unread_count (§6.1).
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupInvite(TimestampMixin, Base):
    __tablename__ = "group_invites"
    __table_args__ = (UniqueConstraint("code", name="uq_group_invites_code"),)

    invite_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("groups.group_id", ondelete="CASCADE"), nullable=False
    )
    # "P2J-K3M9" 형태. 헷갈리는 글자(0/O, 1/I)는 뺀 알파벳으로 만든다.
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
