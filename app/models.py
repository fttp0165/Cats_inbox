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

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
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
