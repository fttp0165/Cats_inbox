# -*- coding: utf-8 -*-
"""資料模型:`app_user` 與 `user_role`。

用途: 本地身分與角色。**不是**使用者資料的真相來源——真相在 Keycloak。
副作用: 無(純宣告)。

🔴 兩條紅線直接長在 schema 上,不是靠程式自律:

1. **業務庫只存 `sub`**(契約 §4.2)。這張表**沒有** email 欄、沒有密碼欄、
   沒有姓名欄。少一個欄位不是「現在沒寫」,是**寫不進去**——
   而「現在沒寫」會在某次趕工時變成「順手存一下」。
2. **`display_name` 是 §4.2a L1 的具名例外**,故它:
   - `nullable`(沒有就是沒有,不得用空字串假裝有);
   - 帶 `display_name_updated_at`(契約 §4.2a L1 第 3 條:UI 要顯示資料時間,
     過期不隱藏而是標示);
   - **不得出現在授權判定路徑上**(以 `tests/test_authz.py` 的源碼檢查釘住)。
"""

from __future__ import annotations

from datetime import datetime, timezone

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    """回傳帶時區的當下時間(UTC)。

    🔴 一律存 UTC。平台的 log 是 UTC 而文件是 UTC+8(portal 憲法第四條7),
    而 PLM_HY 2026-08-12 揭露過「資料庫存的其實是 UTC」這種踩過的坑——
    存 naive 時間會讓跨系統對時變成猜。
    """
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """所有模型的基底。"""


class AppUser(Base):
    """一個本地使用者。

    主鍵是 `sub`(Keycloak 的 subject),不另發本地 id ——
    多一個本地 id 就多一組要對應的東西,而 `sub` 本身已經是穩定且唯一的。
    """

    __tablename__ = "app_user"

    sub: Mapped[str] = mapped_column(String(64), primary_key=True)

    # ── §4.2a L1 具名例外:顯示名稱快取 ──
    # 僅得自**本人登入 token** 取得、僅供管理後台顯示、不進 log、nullable、
    # 附整批+單筆清除工具(`app/repo.py::purge_display_names`)。
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 本地停用:與 IdP 的帳號狀態**分開**(核可條件 C2 的精神——
    # 「還能不能用」不押在 IdP 帳號狀態上)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    def active_roles(self) -> list[str]:
        """回傳**啟用中**的角色名清單(已排序)。

        回傳: list[str]
        副作用: 無

        🔴 「啟用中」是關鍵:被停用的角色列**留在表上**而不是刪掉,
        因為刪掉之後 bootstrap 清單比對會把它當成「還沒給過」而重新授與
        ——那會讓停用在清單成員身上永遠無效(`test_bootstrap_admin_...` 釘住)。
        使用者本身被停用時,一律視為零角色。
        """
        if not self.is_active:
            return []
        return sorted(r.role for r in self.roles if r.enabled)


class UserRole(Base):
    """使用者的一個角色。

    🔴 `enabled` 而不是刪列:見 `AppUser.active_roles` 的說明。
    停用是一個**留下痕跡**的動作,刪除不是。
    """

    __tablename__ = "user_role"
    __table_args__ = (UniqueConstraint("sub", "role", name="uq_user_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sub: Mapped[str] = mapped_column(
        String(64), ForeignKey("app_user.sub", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # 誰給的:`bootstrap`(env 清單)/ `auto`(首登自動授)/ 管理員的 sub。
    # 稽核用途;**不存姓名**,只存 sub 或固定字串。
    granted_by: Mapped[str] = mapped_column(String(64), nullable=False, default="auto")

    user: Mapped[AppUser] = relationship(back_populates="roles")


# ═══════════════════════════════════════════════════════════════════════
# T07:通知核心三張表(欄位名照上游《站內信通知中心設計規劃》§4 逐字沿用)
#
# 🔴 **為什麼不「順手把命名統一」**(`announcement.title` vs `message.subject`):
#    上游 §2.1 的「退路保留」明文寫著「併回 compliance 的成本=**搬四張表**
#    + 改 upstream」。改欄位名會讓那句話**當場失效**——搬表就變成一次資料遷移。
#    用詞不一致是**看得見**的 papercut;改名的代價是**看不見**的。
# ═══════════════════════════════════════════════════════════════════════

CATEGORY_SYSTEM = "system"   # 系統通知(階段一)
CATEGORY_DIRECT = "direct"   # 人對人站內信(階段二)
CATEGORIES = (CATEGORY_SYSTEM, CATEGORY_DIRECT)

AUDIENCE_ALL = "all"
# 🔴 上游 §4.2 的 `audience` 寫「`all` / 群組」,但**群組定向我方做不到**:
#    它需要 `groups` claim,而我方 client **刻意沒有申請 groups**
#    (T03 申請書〈貳〉明載不申請 `email`、不申請 `groups`)。
#    欄位照上游建、約束擋在 `all` —— 因為**建了欄位卻假裝能用**比不建更糟:
#    發布端會以為自己設定了收件範圍,而實際上每一則都送給所有人。
#    要開放群組定向須先向 portal 申請契約 §4.2a **L2**。
AUDIENCES = (AUDIENCE_ALL,)


class Message(Base):
    """一封站內信 / 一則系統通知(**逐人一列**)。

    上游 §4.1。逐人一列的代價是列數,換來的是「已讀狀態就在這一列上」
    ——公告走另一條路(`AnnouncementRead`),因為公告是一則對多人。

    🔴 `body` 一律**純文字**(本專案紅線):stored XSS 是本系統第一風險,
       且同源之下(契約 §4.10)一次 XSS 可觸及 IdP。輸出時必跳脫(T10)。
    🔴 `subject` 與 `body` **不進 log**(本專案紅線);log 只記 id、sub、事件類型。
    """

    __tablename__ = "message"
    __table_args__ = (
        CheckConstraint(
            "category IN ('system', 'direct')", name="ck_message_category"
        ),
        # 未讀鈴鐺:A.1 明訂 **30 秒輪詢**,這個查詢由每一個開著入口首頁的人
        # 每 30 秒跑一次。沒有索引時是全表掃描,而**資料少的時候完全看不出來**;
        # 症狀要等訊息累積起來才出現,而且是「整個入口變慢」不是「inbox 變慢」。
        Index("ix_message_recipient_unread", "recipient_sub", "is_read"),
        # 收件匣列表的排序鍵
        Index("ix_message_recipient_created", "recipient_sub", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # 🔴 **刻意不設外鍵到 `app_user.sub`。** 推送 API(T14)必須能推給
    #    **從未登入過 inbox 的人**——新到職的同事,或 PLM 那 50 位沒有 email 的
    #    測試工程師。設了外鍵,那些推送會被資料庫擋掉,而症狀是「通知沒送到」
    #    ——**收件人自己不會知道有東西被丟掉**。
    #    ⚠ 與 `AnnouncementRead.user_sub`(有外鍵)相反是刻意的,見那邊的說明。
    recipient_sub: Mapped[str] = mapped_column(String(64), nullable=False)
    # null = 系統發出(上游 §4.1)
    sender_sub: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # 🔴 只接受**同站**值(本專案紅線):`/` 開頭的相對路徑,或
    #    `https://catsapp.sporton.com.tw/` 前綴;其餘 400 拒收(寫入時驗,T10)。
    #    站內通知是現成的釣魚載具。
    action_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 🔴 一律由 service client 身分推導,**不得取自 request body**(本專案紅線)
    #    ——能自稱來源就能冒充任何系統發通知。
    source_app: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 階段二(人對人)的對話串;階段一一律 NULL
    thread_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 後端產生,不信前端時間(上游 §4.1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class Announcement(Base):
    """一則公告(**一則對多人**,不逐人複製列)。

    上游 §4.2。有效期以 `starts_at` / `ends_at` 表示;`ends_at` 為 NULL = 無期限。
    """

    __tablename__ = "announcement"
    __table_args__ = (
        CheckConstraint("audience IN ('all')", name="ck_announcement_audience"),
        # 「有效公告」查詢:starts_at <= now AND (ends_at IS NULL OR ends_at > now)
        Index("ix_announcement_window", "starts_at", "ends_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    author_sub: Mapped[str] = mapped_column(String(64), nullable=False)
    # ⚠ 上游 §4.2 用的是 `title`(不是 `subject`)。刻意沿用,見本區塊檔頭。
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(String(32), nullable=False, default=AUDIENCE_ALL)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    reads: Mapped[list["AnnouncementRead"]] = relationship(
        back_populates="announcement", cascade="all, delete-orphan"
    )


class AnnouncementRead(Base):
    """某人讀過某則公告(上游 §4.2)。

    🔴 **這裡設外鍵,而 `Message.recipient_sub` 不設 —— 兩者相反是刻意的:**
       要標記公告已讀,你**必然已經登入過**,所以 `app_user` 那一列必定存在;
       而推送可以送給從未登入過的人。
       ⚠ 這在日後看起來會像不一致,所以理由寫在兩邊的註釋裡,不只寫在日誌。
    """

    __tablename__ = "announcement_read"
    __table_args__ = (
        # 公告不逐人複製列,已讀逐人一列 —— 沒有這個唯一約束時,
        # 重複標已讀會長出重複列,而**已讀在畫面上仍然正常**,只有列數在悄悄長。
        UniqueConstraint("announcement_id", "user_sub", name="uq_announcement_read"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    announcement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("announcement.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_sub: Mapped[str] = mapped_column(
        String(64), ForeignKey("app_user.sub", ondelete="CASCADE"), nullable=False, index=True
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    announcement: Mapped[Announcement] = relationship(back_populates="reads")
