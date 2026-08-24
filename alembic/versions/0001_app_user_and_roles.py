# -*- coding: utf-8 -*-
"""0001:建立 `app_user` 與 `user_role`。

Revision ID: 0001
Revises: None
建立日期: 2026-08-24(T05)

對現有資料的影響:🟢 **不動任何既有資料**——本服務尚未上線,這是第一條
migration,只有 CREATE TABLE,無 UPDATE、無刪除。

🔴 `downgrade()` 真的把兩張表 DROP 掉(共通紅線要求 backward 可回滾)。
   ⚠ **上線後不得如此回滾**:DROP 會連資料一起沒。屆時須先備份並於
   staging 雙向演練(共通紅線)。現階段庫是空的,故可接受。
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建立兩張表。

    🔴 刻意**沒有** email / password / name 欄(契約 §4.2:業務庫只存 sub)。
       `display_name` 是 §4.2a L1 的具名例外,nullable 且帶資料時間戳。
    """
    op.create_table(
        "app_user",
        sa.Column("sub", sa.String(length=64), primary_key=True),
        # §4.2a L1 具名例外:僅自本人登入 token 取得、僅供管理後台顯示
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("display_name_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "user_role",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sub", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        # 🔴 `enabled` 而不是刪列:停用要留下痕跡,否則 bootstrap 清單比對
        #    會把它當成「還沒給過」而重新授與 —— 停用因此永遠無效。
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("granted_by", sa.String(length=64), nullable=False,
                  server_default=sa.text("'auto'")),
        sa.ForeignKeyConstraint(["sub"], ["app_user.sub"], ondelete="CASCADE"),
        sa.UniqueConstraint("sub", "role", name="uq_user_role"),
    )
    op.create_index("ix_user_role_sub", "user_role", ["sub"])


def downgrade() -> None:
    """把兩張表刪掉(順序與建立相反,先刪有外鍵的那張)。"""
    op.drop_index("ix_user_role_sub", table_name="user_role")
    op.drop_table("user_role")
    op.drop_table("app_user")
