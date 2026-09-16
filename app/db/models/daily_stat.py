"""user_daily_stats (ERD v1 §3.7).

하루치 집계 캐시. `todos` 를 매번 세는 대신 이 표를 읽는다.
쓰기 시점은 pending A-5 의 "즉시 반영" 안을 택했다 — todo 가 바뀌는 모든 경로에서
`services/stats.recalc_day()` 가 그날 행을 통째로 다시 계산한다.
부분 갱신(+1/-1)을 하지 않으므로 삭제·미루기·소급 완료에도 값이 어긋나지 않는다.

`streak_count` 는 그날 기준 연속 달성 일수다. 다만 조회용 `current_streak` 는 이 값을 믿지
않고 `services/stats.current_streak()` 가 행을 거꾸로 훑어 계산한다. 과거 날짜를 뒤늦게
고쳤을 때 뒤따르는 날들의 저장값이 낡아 있을 수 있기 때문이다.
"""

from __future__ import annotations

from datetime import date as date_type

from sqlalchemy import Date, ForeignKey, Index, Numeric, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntPK, TimestampMixin


class UserDailyStat(TimestampMixin, Base):
    __tablename__ = "user_daily_stats"
    __table_args__ = (Index("ix_user_daily_stats_user_id_date", "user_id", "date"),)

    user_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date_type] = mapped_column(Date, primary_key=True)

    total_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    done_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    # Numeric(4,3): 0.000 ~ 1.000. asdecimal=False 로 float 로 받는다.
    # Decimal 이 섞이면 JSON 직렬화와 SQLite 테스트에서 타입이 갈린다.
    achievement_rate: Mapped[float] = mapped_column(
        Numeric(4, 3, asdecimal=False), nullable=False, default=0.0
    )
    total_estimated_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    total_actual_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    # 60분 이상 걸린 일을 그날 몇 개 끝냈는지. 계획량 안내(§4)의 유일한 근거 지표다.
    heavy_done_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    streak_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    @property
    def is_cleared(self) -> bool:
        """그날 할 일을 하나 이상 만들고 전부 끝냈는가. 연속 기록의 판정 단위."""
        return self.total_count > 0 and self.done_count >= self.total_count
