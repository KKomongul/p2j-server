"""proofs · comments · reactions (ERD v1 §3.13~§3.15).

저녁에 올리는 인증샷과 거기 달리는 반응.

`proofs` 가 `group_id` · `user_id` 를 중복 저장하는 건 의도된 비정규화다(ERD §2 주석).
피드는 "그룹 + 날짜" 로 조회되는데 정규화를 지키면
`proofs → declaration_items → declarations` 2단 조인이 필요하다. 가장 자주 불리는 API 다.
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
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin

# 서버 화이트리스트. 늘어날 여지가 있어 ENUM 대신 varchar + CHECK (§6.4).
REACTION_TYPES = ("fire", "clap", "heart", "muscle")


class Proof(TimestampMixin, Base):
    __tablename__ = "proofs"
    __table_args__ = (
        # 선언 항목 하나에 인증 하나 → 422 PROOF_ALREADY_EXISTS (§6.3)
        UniqueConstraint("declaration_item_id", name="uq_proofs_declaration_item_id"),
        Index(
            "ix_proofs_group_id_created_at",
            "group_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_proofs_group_id_date", "group_id", "date"),
    )

    post_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    declaration_item_id: Mapped[int] = mapped_column(
        BigIntPK,
        ForeignKey("declaration_items.declaration_item_id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("groups.group_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    # 선언 날짜. 피드가 날짜로 묶이므로 created_at 에서 매번 계산하지 않고 들고 있는다.
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    # 스토리지 객체 키. 파일 자체는 서버를 거치지 않는다 (§6.3 presign).
    file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    caption: Mapped[str | None] = mapped_column(String(200))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Comment(TimestampMixin, Base):
    __tablename__ = "comments"
    __table_args__ = (
        Index(
            "ix_comments_post_id_comment_id",
            "post_id",
            "comment_id",
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    comment_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("proofs.post_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(String(500), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reaction(TimestampMixin, Base):
    """사용자당 1개. PUT 은 UPSERT 로 동작한다 (§6.4)."""

    __tablename__ = "reactions"
    __table_args__ = (
        CheckConstraint("type IN ('fire', 'clap', 'heart', 'muscle')", name="type"),
        UniqueConstraint("post_id", "user_id", name="uq_reactions_post_id_user_id"),
    )

    reaction_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("proofs.post_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(10), nullable=False)
