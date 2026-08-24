# -*- coding: utf-8 -*-
"""0002:建立 `message` / `announcement` / `announcement_read`(T07)。

Revision ID: 0002
Revises: 0001
建立日期: 2026-08-24(T07)

對現有資料的影響:🟢 **不動任何既有資料**——只有 CREATE TABLE。
`0001` 建的 `app_user` / `user_role` 一欄未動。

🔴 `downgrade()` 只刪本 revision 建的三張表,**不得動到 `0001` 的表**
   ——那在正式環境是把所有人的身分與角色刪掉。
   `tests/test_schema.py::test_migration_0002_up_down_up` 對此有斷言。

欄位名一律照上游《站內信通知中心設計規劃》**§4** 逐字沿用
(含 `announcement.title` 與 `message.subject` 用詞不一致)。
理由:上游 §2.1 的「退路=**搬四張表**」在改名之後會變成一次資料遷移。
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建立三張表、兩組 CHECK、三個索引、一個唯一約束。"""
    op.create_table(
        "message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # 🔴 **刻意不設外鍵到 `app_user.sub`。** 推送 API(T14)必須能推給
        #    **從未登入過 inbox 的人**(新到職者、PLM 那 50 位沒有 email 的
        #    測試工程師)。設了外鍵,那些推送會被資料庫擋掉,而症狀是
        #    「通知沒送到」——**收件人自己不會知道有東西被丟掉**。
        #    ⚠ 與下方 `announcement_read.user_sub`(**有**外鍵)相反是刻意的。
        sa.Column("recipient_sub", sa.String(length=64), nullable=False),
        sa.Column("sender_sub", sa.String(length=64), nullable=True),  # null=系統發出
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        # body 一律 TEXT:純文字通知可能很長,而 VARCHAR(n) 超長在 PG 上是**報錯**,
        # 症狀會是「某些通知推不進來」。
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("action_url", sa.String(length=512), nullable=True),
        sa.Column("source_app", sa.String(length=32), nullable=True),
        sa.Column("thread_id", sa.Uuid(), nullable=True),   # 階段二(人對人)
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # 用 CHECK 而非 PG 的 enum 型別:enum 要加值得下 `ALTER TYPE`,
        # 而舊版 PG **無法從 enum 移除值** —— 那個 migration 寫不出 downgrade。
        sa.CheckConstraint("category IN ('system', 'direct')", name="ck_message_category"),
    )
    # 未讀鈴鐺(A.1:30 秒輪詢)——每個開著入口首頁的人每 30 秒跑一次這個查詢。
    # 沒有索引時是全表掃描,而**資料少的時候完全看不出來**。
    op.create_index("ix_message_recipient_unread", "message", ["recipient_sub", "is_read"])
    op.create_index("ix_message_recipient_created", "message", ["recipient_sub", "created_at"])

    op.create_table(
        "announcement",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("author_sub", sa.String(length=64), nullable=False),
        # ⚠ 上游 §4.2 用的是 `title`(不是 `subject`)。刻意沿用。
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audience", sa.String(length=32), nullable=False,
                  server_default=sa.text("'all'")),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),  # null=無期限
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # 🔴 上游支援「群組」定向,但**我方做不到**:那需要 `groups` claim,
        #    而我方 client **刻意沒有申請 groups**(T03 申請書〈貳〉)。
        #    欄位照上游建、約束擋在 `all` —— **建了欄位卻假裝能用**比不建更糟:
        #    發布端會以為自己設定了收件範圍,而實際上每一則都送給所有人。
        #    要開放須先向 portal 申請契約 §4.2a **L2**。
        sa.CheckConstraint("audience IN ('all')", name="ck_announcement_audience"),
    )
    op.create_index("ix_announcement_window", "announcement", ["starts_at", "ends_at"])

    op.create_table(
        "announcement_read",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("announcement_id", sa.Uuid(), nullable=False),
        # 🔴 **這裡設外鍵,而 `message.recipient_sub` 不設 —— 相反是刻意的:**
        #    要標記公告已讀,你**必然已經登入過**,所以 `app_user` 那一列必定存在。
        sa.Column("user_sub", sa.String(length=64), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcement.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_sub"], ["app_user.sub"], ondelete="CASCADE"),
        # 公告不逐人複製列,已讀逐人一列。沒有這個唯一約束時,重複標已讀會
        # 長出重複列,而**已讀在畫面上仍然正常**,只有列數在悄悄長。
        sa.UniqueConstraint("announcement_id", "user_sub", name="uq_announcement_read"),
    )
    op.create_index("ix_announcement_read_announcement_id", "announcement_read",
                    ["announcement_id"])
    op.create_index("ix_announcement_read_user_sub", "announcement_read", ["user_sub"])


def downgrade() -> None:
    """只刪本 revision 建的三張表(依外鍵相依的反序)。

    🔴 **不得動到 `0001` 的 `app_user` / `user_role`** —— 那在正式環境是
       把所有人的身分與角色刪掉。測試對此有明確斷言。
    """
    op.drop_index("ix_announcement_read_user_sub", table_name="announcement_read")
    op.drop_index("ix_announcement_read_announcement_id", table_name="announcement_read")
    op.drop_table("announcement_read")
    op.drop_index("ix_announcement_window", table_name="announcement")
    op.drop_table("announcement")
    op.drop_index("ix_message_recipient_created", table_name="message")
    op.drop_index("ix_message_recipient_unread", table_name="message")
    op.drop_table("message")
