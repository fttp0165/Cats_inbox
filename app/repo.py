# -*- coding: utf-8 -*-
"""身分與角色的資料存取(唯一會寫 `app_user` / `user_role` 的地方)。

用途: 首登建號、bootstrap 清單比對、角色授與/停用、L1 快取清除。
副作用: **寫資料庫**。每個函式都標明它寫了什麼。

🔴 `display_name` 的**唯一寫入路徑**在本檔的 `ensure_user_on_login()`。
   以 `tests/test_authz.py::test_display_name_only_written_from_own_login_token`
   的 AST 檢查釘住:`app/` 底下其他檔案對 `display_name` 賦值即紅燈。
   理由:契約 §4.2a L1 要求「僅得自本人登入 token 取得」,而多開一條寫入路徑
   **不會有錯誤訊息**,只會讓那句話變成一句話。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authz import ALL_ROLES, ROLE_ADMIN, ROLE_READER
from app.models import AppUser, UserRole


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_user(session: Session, sub: str) -> AppUser | None:
    """取使用者;不存在回 None。"""
    return session.get(AppUser, sub)


def grant_role(session: Session, sub: str, role: str, *, granted_by: str = "auto") -> UserRole:
    """授與角色(冪等)。

    參數: sub;role — `ALL_ROLES` 之一;granted_by — `auto`/`bootstrap`/管理員的 sub
    回傳: UserRole
    副作用: 可能 INSERT 一列 `user_role`

    🔴 **已存在但被停用的角色不會被重新啟用。** 這是刻意的:
    停用之後又被「授與」一次就自動復活,會讓停用在任何自動路徑
    (首登自動授、bootstrap 清單)上永遠無效——而畫面上完全正常。
    要重新啟用請明確呼叫 `set_role_enabled(..., enabled=True)`。
    """
    if role not in ALL_ROLES:
        raise ValueError(f"未知角色:{role}")
    existing = session.scalar(select(UserRole).where(UserRole.sub == sub, UserRole.role == role))
    if existing is not None:
        return existing
    row = UserRole(sub=sub, role=role, enabled=True, granted_by=granted_by, granted_at=_utcnow())
    session.add(row)
    session.flush()
    return row


def set_role_enabled(session: Session, sub: str, role: str, *, enabled: bool) -> bool:
    """啟用/停用某人的某個角色。

    參數: enabled — True 啟用、False 停用
    回傳: 是否有找到那一列
    副作用: UPDATE 一列 `user_role`

    這就是核可條件 **C2**(`reader` 須可由本專案後台單獨停用)的落點:
    停用**只動這一個角色**,不動使用者本身、不動其他角色、不碰 IdP。
    """
    row = session.scalar(select(UserRole).where(UserRole.sub == sub, UserRole.role == role))
    if row is None:
        return False
    row.enabled = enabled
    session.flush()
    return True


def purge_display_names(session: Session, *, sub: str | None = None) -> int:
    """清除 `display_name` 快取(單筆或整批)。

    參數: sub — 給值則只清那一個;None 則**整批**
    回傳: 實際清掉幾列
    副作用: UPDATE `app_user`(把 `display_name` 與其時間戳設為 NULL)

    契約 §4.2a L1 第 7 條要求附整批清除工具。
    ⚠ 只清**副本**——真相來源是 Keycloak,而使用者本身與角色一律不動。
    🔴 回傳「實際清掉幾列」而不是回 None:一個永遠成功卻什麼都沒清的工具
    比沒有工具更糟,它讓人以為已經清了。
    """
    stmt = select(AppUser).where(AppUser.display_name.is_not(None))
    if sub is not None:
        stmt = stmt.where(AppUser.sub == sub)
    rows = list(session.scalars(stmt))
    for row in rows:
        row.display_name = None
        row.display_name_updated_at = None
    session.flush()
    return len(rows)


def ensure_user_on_login(
    session: Session,
    *,
    sub: str,
    display_name: str | None,
    bootstrap_admin_subs: str,
    auto_grant_reader: bool,
) -> AppUser:
    """首登建號 / 每次登入的身分維護。

    參數:
      sub                  — 來自**已驗簽**的 id_token
      display_name         — 同一個 token 的 `name` claim(可為 None)
      bootstrap_admin_subs — 逗號分隔的 sub 清單(env)
      auto_grant_reader    — DEC-16 的全域開關;False 即回到全 deny(條件 C4)
    回傳: AppUser
    副作用: 可能 INSERT `app_user`、INSERT `user_role`、UPDATE 快取與登入時間

    四件事,順序是刻意的:

    1. **建號或取號**(冪等——同一個 `sub` 不得長出第二列);
    2. **寫 L1 快取**:只有這裡寫 `display_name`,且只用本人這次 token 的值;
    3. **自動授 `reader`**(DEC-16),受全域開關控制;
    4. 🔴 **bootstrap 管理員清單每次登入都比對**,不只建號當下 ——
       upload-program 踩過:只在建號當下比對,對**第一個管理員**永遠不會生效,
       因為他早就登入過了,而那次登入時清單還是空的(契約 §4.3)。
       ⚠ 而它**不會復活已停用的角色**(見 `grant_role` 的說明)。
    """
    user = session.get(AppUser, sub)
    if user is None:
        user = AppUser(sub=sub, is_active=True, created_at=_utcnow())
        session.add(user)
        session.flush()

    # ── L1 快取(唯一寫入路徑)──
    if display_name:
        user.display_name = display_name
        user.display_name_updated_at = _utcnow()
    user.last_login_at = _utcnow()

    # ── 自動授 reader(僅此一個角色;其餘一律 deny-by-default)──
    if auto_grant_reader:
        grant_role(session, sub, ROLE_READER, granted_by="auto")

    # ── bootstrap 管理員清單 ──
    wanted = {s.strip() for s in bootstrap_admin_subs.split(",") if s.strip()}
    if sub in wanted:
        grant_role(session, sub, ROLE_ADMIN, granted_by="bootstrap")

    session.flush()
    return user
