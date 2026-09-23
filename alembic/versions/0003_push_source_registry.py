# -*- coding: utf-8 -*-
"""0003:建立 `source_app`(來源登記表)與 `push_receipt`(去重收據)(T14a)。

Revision ID: 0003
Revises: 0002
建立日期: 2026-09-23(T14a)

對現有資料的影響:🟡 **加表** —— 只有 CREATE TABLE(含索引、唯一約束、外鍵)。
`0001` 的 `app_user` / `user_role`、`0002` 的 `message` / `announcement` /
`announcement_read` **一欄未動**。

🔴 **刻意不對 `message` 加欄位**:上游 §2.1 的退路是「搬四張表」,
   S2S 專用的欄位長在 `message` 上,搬表就變成一次資料遷移。

🔴 `downgrade()` 只刪本 revision 建的兩張表 —— **不得動到 `message`**
   (那在正式環境是刪掉所有人的通知)。
   `tests/test_schema.py::test_migration_0003_up_down_up` 對此有斷言。
   ⚠ 上線後回滾前必先備份:`push_receipt` 同時是 `X-User-Id` 的稽核紀錄。
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建立兩張表、一個唯一約束、兩個外鍵、一個索引。"""
    op.create_table(
        "source_app",
        # Keycloak 的 client_id 上限是 255
        sa.Column("azp", sa.String(length=255), primary_key=True),
        # 🔴 欄寬**必須 ≤ `message.source_app`(32)**:標籤會被原樣複製過去,
        #    大於它的話,每一次推送都在 INSERT 時 500(PG 對超長是報錯)。
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "push_receipt",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("azp", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        # `X-User-Id`:觸發事件的人(稽核鏈)。只存 sub。
        sa.Column("actor_sub", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # 🔴 key 以**呼叫方**為範圍;也是並行重送時唯一擋住重複訊息的東西
        sa.UniqueConstraint("azp", "idempotency_key", name="uq_push_receipt_azp_key"),
        # 收據的保留期跟著訊息走(N3 定案後刪舊訊息時一起走,不留孤兒)
        sa.ForeignKeyConstraint(["message_id"], ["message.id"], ondelete="CASCADE"),
        # ⚠ **不** CASCADE:登記表只停用不刪除;有歷史的來源刪不掉是對的
        sa.ForeignKeyConstraint(["azp"], ["source_app.azp"]),
    )
    op.create_index("ix_push_receipt_message_id", "push_receipt", ["message_id"])


def downgrade() -> None:
    """只刪本 revision 建的兩張表(依外鍵相依的反序)。

    🔴 **不得動到 `0001` / `0002` 的表** —— 測試對此有明確斷言。
    """
    op.drop_index("ix_push_receipt_message_id", table_name="push_receipt")
    op.drop_table("push_receipt")
    op.drop_table("source_app")
