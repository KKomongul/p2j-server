"""load_checks (ERD v1 §3.6). 계획량 안내의 판정 결과와 사용자의 응답.

UNIQUE(user_id, date) — 하루 한 번만 판정하고, 재조회는 저장된 행을 그대로 돌려준다.
같은 날 안에서 안내 문구가 바뀌면 "아까는 괜찮다더니" 가 되기 때문이다.

`check_id` 는 ERD 의 uuid 대신 `chk_` 접두사 + 8 hex 로 둔다(pending A-4 의 (b)안).
`ai_parse` 의 `prs_` 와 형식을 맞췄고, 클라이언트에는 불투명 문자열이다.
"""

from __future__ import annotations

import secrets
from datetime import date as date_type
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, BigIntPK, TimestampMixin

CHECK_LEVELS = ("ok", "warning")

# PostgreSQL 에서는 jsonb, SQLite(테스트)에서는 JSON 텍스트.
JsonColumn = JSON().with_variant(JSONB(), "postgresql")


def new_check_id() -> str:
    return f"chk_{secrets.token_hex(4)}"


class LoadCheck(TimestampMixin, Base):
    __tablename__ = "load_checks"
    __table_args__ = (
        CheckConstraint("level IN ('ok', 'warning')", name="level"),
        # 하루 한 번을 DB 레벨에서 보장한다. 동시 요청 두 개가 겹쳐도 한 행만 남는다.
        UniqueConstraint("user_id", "date", name="uq_load_checks_user_id_date"),
    )

    check_id: Mapped[str] = mapped_column(String(20), primary_key=True, default=new_check_id)
    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False, default="ok")
    # 근거가 없으면 문구를 만들지 않는다. NULL 은 "안내할 말이 없음"이다.
    message: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JsonColumn, nullable=False, default=dict)
    suggestions_json: Mapped[list[Any]] = mapped_column(JsonColumn, nullable=False, default=list)
    # NULL = 아직 응답하지 않음. 이 분포가 곧 안내 기능의 유효성 지표가 된다 (§4).
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    applied_todo_ids: Mapped[list[int]] = mapped_column(JsonColumn, nullable=False, default=list)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
