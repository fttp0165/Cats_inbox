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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.authz import ALL_ROLES, ROLE_ADMIN, ROLE_READER
from app.models import AppUser, Message, UserRole


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


# ═══════════════════════════════════════════════════════════════════════
# T08:訊息讀取
#
# 🔴 這三個函式的共同紅線:**收件人一律由呼叫方傳入的 `sub` 決定,
#    而那個 `sub` 一律來自已驗簽的 token,不是來自 request。**
#    每一個函式都有 `recipient_sub` 參數且**必填** —— 沒有「查全部」的版本,
#    因為那個版本一旦存在,就會有人在某個端點上不小心用到它。
# ═══════════════════════════════════════════════════════════════════════


def list_messages(
    session: Session, *, recipient_sub: str, unread_only: bool = False, limit: int = 50
) -> list[Message]:
    """列出某人的訊息(新的在前)。

    參數:
      recipient_sub — **必填**,來自 token 的 `sub`
      unread_only   — True 時只回未讀
      limit         — 上限;預設 50
    回傳: list[Message]
    副作用: 無(只讀)

    🔴 `recipient_sub` 沒有預設值也沒有「不傳就查全部」的分支。
       那個分支若存在,少寫一個參數的呼叫端就會把全公司的通知端出去,
       而**畫面看起來完全正常**(只是訊息比較多)。
    """
    stmt = select(Message).where(Message.recipient_sub == recipient_sub)
    if unread_only:
        stmt = stmt.where(Message.is_read.is_(False))
    stmt = stmt.order_by(Message.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))


def count_unread(session: Session, *, recipient_sub: str) -> int:
    """數某人的未讀訊息。

    參數: recipient_sub — **必填**,來自 token 的 `sub`
    回傳: int
    副作用: 無(只讀)

    ⚠ 這是未讀鈴鐺的查詢(A.1:**30 秒輪詢**),由每一個開著入口首頁的人
    每 30 秒跑一次。用 `count(*)` 而不是把列撈出來再 `len()` ——
    後者在訊息累積之後會把整個收件匣搬進記憶體,而**在資料少的時候
    兩者的觀測結果完全相同**。走 `ix_message_recipient_unread` 索引。
    """
    stmt = (
        select(func.count())
        .select_from(Message)
        .where(Message.recipient_sub == recipient_sub, Message.is_read.is_(False))
    )
    return int(session.scalar(stmt) or 0)


def mark_message_read(session: Session, *, message_id, recipient_sub: str) -> Message | None:
    """把某人的某一則訊息標為已讀(**冪等**)。

    參數: message_id;recipient_sub — **必填**,來自 token 的 `sub`
    回傳: Message(成功)/ None(不是這個人的訊息,或不存在)
    副作用: 可能 UPDATE `is_read` / `read_at` 兩欄

    🔴 **冪等的語意是「第二次呼叫不改變 `read_at`」**,不只是「不報錯」。
       被覆寫的話,「這則是什麼時候讀的」永遠是最後一次點擊的時間,
       那個欄位就沒有意義了 —— 而兩次呼叫都回 200,不會有任何錯誤訊息。

    🔴 查詢條件同時帶 `id` **與** `recipient_sub`:不是先查出來再比對。
       先查再比對的寫法會多一條「查到了但忘記比對」的路徑,
       而那條路徑讓任何人拿到 id 就能標別人的訊息。
    """
    row = session.scalar(
        select(Message).where(Message.id == message_id, Message.recipient_sub == recipient_sub)
    )
    if row is None:
        return None
    if not row.is_read:
        row.is_read = True
        row.read_at = _utcnow()
        session.flush()
    return row
