"""declarations · declaration_items (ERD v1 §3.11~§3.12).

아침에 "오늘 이거 합니다" 를 그룹에 못 박는 기능. 선언은 생성 즉시 잠긴다 (§6.2).

`title_snapshot` 을 따로 저장하는 이유(ERD §2.2): 선언 후 할 일 제목이 바뀌거나
할 일이 지워져도, 그룹 사람들이 아침에 본 문장은 그대로 남아야 한다.
`todo_id` 는 ON DELETE SET NULL — 원본이 사라져도 선언 기록은 남는다.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BigIntPK, TimestampMixin


class Declaration(TimestampMixin, Base):
    __tablename__ = "declarations"
    __table_args__ = (
        # 하루 한 번을 DB 가 보장한다 → 409 DECLARATION_ALREADY_EXISTS (§6.2)
        UniqueConstraint(
            "group_id", "user_id", "date", name="uq_declarations_group_id_user_id_date"
        ),
        Index("ix_declarations_group_id_date", "group_id", "date"),
    )

    declaration_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("groups.group_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    # 생성 즉시 채운다. NULL 인 선언은 존재하지 않는다.
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    items: Mapped[list[DeclarationItem]] = relationship(
        "DeclarationItem",
        back_populates="declaration",
        cascade="all, delete-orphan",
        order_by="DeclarationItem.declaration_item_id",
        lazy="selectin",
    )


class DeclarationItem(TimestampMixin, Base):
    __tablename__ = "declaration_items"
    __table_args__ = (
        UniqueConstraint(
            "declaration_id", "todo_id", name="uq_declaration_items_declaration_id_todo_id"
        ),
    )

    declaration_item_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    declaration_id: Mapped[int] = mapped_column(
        BigIntPK,
        ForeignKey("declarations.declaration_id", ondelete="CASCADE"),
        nullable=False,
    )
    todo_id: Mapped[int | None] = mapped_column(
        BigIntPK, ForeignKey("todos.todo_id", ondelete="SET NULL")
    )
    title_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)

    declaration: Mapped[Declaration] = relationship("Declaration", back_populates="items")
