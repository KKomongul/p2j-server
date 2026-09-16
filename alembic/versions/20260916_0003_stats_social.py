"""user_daily_stats, load_checks, groups, declarations, proofs

Revision ID: 0003_stats_social
Revises: 0002_goals_todos
Create Date: 2026-09-16

손으로 작성했다. app/db/models/ 의 daily_stat · load_check · group · declaration · proof 와
1:1 이어야 CI 의 `alembic check` 가 통과한다.

기존 테이블은 건드리지 않는다. 전부 새 테이블이라 되돌리기도 drop 만으로 끝난다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_stats_social"
down_revision: str | None = "0002_goals_todos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 모델의 JsonColumn 과 같은 타입. PostgreSQL 에서만 jsonb 로 떨어진다.
JSON_COLUMN = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    # ---- 집계 ------------------------------------------------------------------
    op.create_table(
        "user_daily_stats",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("total_count", sa.SmallInteger(), nullable=False),
        sa.Column("done_count", sa.SmallInteger(), nullable=False),
        sa.Column("achievement_rate", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("total_estimated_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("total_actual_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("heavy_done_count", sa.SmallInteger(), nullable=False),
        sa.Column("streak_count", sa.SmallInteger(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("user_id", "date", name="pk_user_daily_stats"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_user_daily_stats_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_user_daily_stats_user_id_date", "user_daily_stats", ["user_id", "date"]
    )

    op.create_table(
        "load_checks",
        sa.Column("check_id", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("evidence_json", JSON_COLUMN, nullable=False),
        sa.Column("suggestions_json", JSON_COLUMN, nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.Column("applied_todo_ids", JSON_COLUMN, nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("check_id", name="pk_load_checks"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name="fk_load_checks_user_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint("level IN ('ok', 'warning')", name="level"),
        sa.UniqueConstraint("user_id", "date", name="uq_load_checks_user_id_date"),
    )

    # ---- 그룹 ------------------------------------------------------------------
    op.create_table(
        "groups",
        sa.Column("group_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("max_members", sa.SmallInteger(), server_default="6", nullable=False),
        sa.Column("group_streak", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("last_streak_date", sa.Date(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("group_id", name="pk_groups"),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.user_id"], name="fk_groups_owner_id_users", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("max_members BETWEEN 3 AND 6", name="max_members"),
        sa.CheckConstraint("group_streak >= 0", name="group_streak"),
    )

    op.create_table(
        "group_members",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_group_members"),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.group_id"],
            name="fk_group_members_group_id_groups",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_group_members_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("role IN ('owner', 'member')", name="role"),
    )
    # 부분 유니크: 탈퇴(left_at) 후 재가입은 되고 중복 가입은 막힌다 (ERD §3.9).
    op.create_index(
        "uq_group_members_group_id_user_id_active",
        "group_members",
        ["group_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
        sqlite_where=sa.text("left_at IS NULL"),
    )
    op.create_index(
        "ix_group_members_user_id_active",
        "group_members",
        ["user_id"],
        postgresql_where=sa.text("left_at IS NULL"),
        sqlite_where=sa.text("left_at IS NULL"),
    )

    op.create_table(
        "group_invites",
        sa.Column("invite_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("invite_id", name="pk_group_invites"),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.group_id"],
            name="fk_group_invites_group_id_groups",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.user_id"],
            name="fk_group_invites_created_by_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("code", name="uq_group_invites_code"),
    )

    # ---- 선언 ------------------------------------------------------------------
    op.create_table(
        "declarations",
        sa.Column("declaration_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("declaration_id", name="pk_declarations"),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.group_id"],
            name="fk_declarations_group_id_groups",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name="fk_declarations_user_id_users",
            ondelete="CASCADE",
        ),
        # 하루 한 번을 DB 가 보장한다 → 409 DECLARATION_ALREADY_EXISTS
        sa.UniqueConstraint(
            "group_id", "user_id", "date", name="uq_declarations_group_id_user_id_date"
        ),
    )
    op.create_index("ix_declarations_group_id_date", "declarations", ["group_id", "date"])

    op.create_table(
        "declaration_items",
        sa.Column(
            "declaration_item_id", sa.BigInteger(), sa.Identity(always=False), nullable=False
        ),
        sa.Column("declaration_id", sa.BigInteger(), nullable=False),
        sa.Column("todo_id", sa.BigInteger(), nullable=True),
        sa.Column("title_snapshot", sa.String(length=100), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("declaration_item_id", name="pk_declaration_items"),
        sa.ForeignKeyConstraint(
            ["declaration_id"],
            ["declarations.declaration_id"],
            name="fk_declaration_items_declaration_id_declarations",
            ondelete="CASCADE",
        ),
        # 원본 할 일이 사라져도 "아침에 약속했다" 는 기록은 남는다 (ERD §2.2).
        sa.ForeignKeyConstraint(
            ["todo_id"],
            ["todos.todo_id"],
            name="fk_declaration_items_todo_id_todos",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "declaration_id", "todo_id", name="uq_declaration_items_declaration_id_todo_id"
        ),
    )

    # ---- 인증·반응 --------------------------------------------------------------
    op.create_table(
        "proofs",
        sa.Column("post_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("declaration_item_id", sa.BigInteger(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("file_key", sa.String(length=500), nullable=False),
        sa.Column("caption", sa.String(length=200), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("post_id", name="pk_proofs"),
        sa.ForeignKeyConstraint(
            ["declaration_item_id"],
            ["declaration_items.declaration_item_id"],
            name="fk_proofs_declaration_item_id_declaration_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["groups.group_id"], name="fk_proofs_group_id_groups", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name="fk_proofs_user_id_users", ondelete="CASCADE"
        ),
        # 선언 항목 하나에 인증 하나 → 422 PROOF_ALREADY_EXISTS
        sa.UniqueConstraint("declaration_item_id", name="uq_proofs_declaration_item_id"),
    )
    op.create_index(
        "ix_proofs_group_id_created_at",
        "proofs",
        ["group_id", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_proofs_group_id_date", "proofs", ["group_id", "date"])

    op.create_table(
        "comments",
        sa.Column("comment_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("post_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("comment_id", name="pk_comments"),
        sa.ForeignKeyConstraint(
            ["post_id"], ["proofs.post_id"], name="fk_comments_post_id_proofs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name="fk_comments_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_comments_post_id_comment_id",
        "comments",
        ["post_id", "comment_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "reactions",
        sa.Column("reaction_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("post_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(length=10), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("reaction_id", name="pk_reactions"),
        sa.ForeignKeyConstraint(
            ["post_id"], ["proofs.post_id"], name="fk_reactions_post_id_proofs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name="fk_reactions_user_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint("type IN ('fire', 'clap', 'heart', 'muscle')", name="type"),
        # 사용자당 1개. PUT 이 곧 UPSERT 가 된다 (§6.4).
        sa.UniqueConstraint("post_id", "user_id", name="uq_reactions_post_id_user_id"),
    )


def downgrade() -> None:
    op.drop_table("reactions")
    op.drop_index("ix_comments_post_id_comment_id", table_name="comments")
    op.drop_table("comments")
    op.drop_index("ix_proofs_group_id_date", table_name="proofs")
    op.drop_index("ix_proofs_group_id_created_at", table_name="proofs")
    op.drop_table("proofs")
    op.drop_table("declaration_items")
    op.drop_index("ix_declarations_group_id_date", table_name="declarations")
    op.drop_table("declarations")
    op.drop_table("group_invites")
    op.drop_index("ix_group_members_user_id_active", table_name="group_members")
    op.drop_index("uq_group_members_group_id_user_id_active", table_name="group_members")
    op.drop_table("group_members")
    op.drop_table("groups")
    op.drop_table("load_checks")
    op.drop_index("ix_user_daily_stats_user_id_date", table_name="user_daily_stats")
    op.drop_table("user_daily_stats")
