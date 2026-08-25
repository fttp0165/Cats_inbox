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

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.authz import ALL_ROLES, ROLE_ADMIN, ROLE_READER
from app.models import (
    AUDIENCE_ALL,
    CATEGORIES,
    CATEGORY_SYSTEM,
    Announcement,
    AnnouncementRead,
    AppUser,
    Message,
    UserRole,
)


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


# ═══════════════════════════════════════════════════════════════════════
# T09:公告(一則對多人)+ 逐人已讀
#
# 🔴 **公告與訊息的根本差別,決定了這三個函式長什麼樣:**
#    訊息是**逐人一列**,已讀狀態就在那一列上;
#    公告是**一則對多人**,已讀必須放在另一張表 —— 因為一萬個人的公告
#    若逐人複製,那是一萬列**內容相同**的資料。
#    所以「已讀」在這裡不是公告的屬性,是 **(公告, 人)** 這個配對的屬性。
#    把它寫成公告的欄位會讓一個人讀完全公司都變成已讀,
#    而其他人只是覺得自己**好像看過** —— 沒有人會回報那是 bug。
# ═══════════════════════════════════════════════════════════════════════


def create_announcement(
    session: Session,
    *,
    author_sub: str,
    title: str,
    body: str,
    starts_at: datetime,
    ends_at: datetime | None,
    audience: str = AUDIENCE_ALL,
) -> Announcement:
    """建立一則公告。

    參數:
      author_sub — **一律來自已驗簽的 token**,不得取自 request body
      starts_at  — 生效時間(帶時區);ends_at — 失效時間,None = 無期限
    回傳: Announcement
    副作用: INSERT 一列 `announcement`

    🔴 值的合法性(空標題、超長、無時區、空窗、未知 audience)由
       `app/validation.py` 在**進到這裡之前**擋掉。本函式不再重驗一次:
       兩處各驗一半的話,兩邊遲早會漂移,而漂移之後**寬的那一邊贏**。
    """
    row = Announcement(
        author_sub=author_sub,
        title=title,
        body=body,
        audience=audience,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    session.add(row)
    session.flush()
    return row


def list_active_announcements(
    session: Session, *, user_sub: str, now: datetime | None = None
) -> list[tuple[Announcement, bool]]:
    """列出**目前有效**的公告,附帶「這個人讀過沒」。

    參數: user_sub — 來自 token 的 `sub`;now — 判定用的當下(測試可注入)
    回傳: [(Announcement, 我讀過沒), ...],新的在前
    副作用: 無(只讀)

    有效期語意 **`starts_at <= now < ends_at`**(起含、迄不含;`ends_at`
    為 NULL = 無期限)。⚠ 這與 `app/models.py` 裡 `ix_announcement_window`
    的註釋必須**逐字一致** —— 兩邊漂移的話索引還在、查詢也還跑得動,
    只是條件悄悄變了。

    🔴 **兩個條件都要驗。** 只驗 `ends_at` 是最容易漏的一半:
       排程下週的公告會**當場就出現**,而發布者以為排程生效了。

    🔴 **`user_sub` 必須寫在 JOIN 的 ON 裡,不能搬到 WHERE。**
       搬到 WHERE 的話,LEFT JOIN 對「別人讀過而我沒讀」的公告會配出
       **別人的**已讀列,然後被 WHERE 濾掉 ——
       結果是**別人一讀,那則公告就從我的清單裡消失**。
       釘住它的是 `test_read_by_one_person_does_not_mark_it_read_for_others`。
    """
    at = now or _utcnow()
    stmt = (
        select(Announcement, AnnouncementRead.id)
        .outerjoin(
            AnnouncementRead,
            and_(
                AnnouncementRead.announcement_id == Announcement.id,
                AnnouncementRead.user_sub == user_sub,
            ),
        )
        .where(
            Announcement.starts_at <= at,
            or_(Announcement.ends_at.is_(None), Announcement.ends_at > at),
        )
        .order_by(Announcement.starts_at.desc())
    )
    return [(ann, read_id is not None) for ann, read_id in session.execute(stmt)]


def mark_announcement_read(
    session: Session, *, announcement_id, user_sub: str
) -> AnnouncementRead | None:
    """標記某人讀過某則公告(**冪等**)。

    參數: announcement_id;user_sub — 來自 token 的 `sub`
    回傳: AnnouncementRead(成功)/ None(**公告不存在**)
    副作用: 首次呼叫 INSERT 一列 `announcement_read`;第二次**什麼都不做**

    🔴 **冪等的語意是「第二次呼叫不新增列、也不改 `read_at`」**,
       不只是「不報錯」。
       - 新增列:表上有唯一約束會擋住 ——但**只擋得住並行以外的重複**,
         而且撞上去是 IntegrityError(500)。先查再寫,拒收在前;
       - 改 `read_at`:被覆寫的話,「這則是什麼時候讀的」永遠是最後一次
         點擊的時間,那個欄位就沒有意義了 —— 而兩次呼叫都回 200。

    ⚠ **已過期的公告仍可標已讀**,刻意不擋:多一個「還在有效期內嗎」的分支
       只會多一種讓使用者按了沒反應的情況,而標記一則過期公告已讀無害。
    """
    if session.get(Announcement, announcement_id) is None:
        return None
    existing = session.scalar(
        select(AnnouncementRead).where(
            AnnouncementRead.announcement_id == announcement_id,
            AnnouncementRead.user_sub == user_sub,
        )
    )
    if existing is not None:
        return existing
    row = AnnouncementRead(
        announcement_id=announcement_id, user_sub=user_sub, read_at=_utcnow()
    )
    session.add(row)
    session.flush()
    return row


# ═══════════════════════════════════════════════════════════════════════
# T10:訊息的**唯一寫入路徑**
#
# 🔴 為什麼關卡設在這裡,而不是設在端點上:
#    `action_url` 的唯一寫入端點是**推送 API(T14)**,而它還不存在。
#    設在端點上等於在一個還不存在的地方設關卡 —— 而 T14 動工時,
#    授權與驗證是那次的**附帶工作**,附帶工作是最容易被忘記的那一種。
#    設在寫入路徑上,T14 就**只能穿過它**。
#
# 🔴 以源碼層 AST 檢查釘住「`app/` 底下只有這裡構造 `Message`」
#    (`tests/test_security.py::test_message_is_only_constructed_in_repo_create_message`)。
#    行為測試只能證明「這條路徑現在有驗」;源碼檢查證明「沒有第二條路徑」。
# ═══════════════════════════════════════════════════════════════════════


def create_message(
    session: Session,
    *,
    recipient_sub: str,
    subject: str,
    body: str,
    action_url: str | None = None,
    source_app: str | None = None,
    sender_sub: str | None = None,
    category: str = CATEGORY_SYSTEM,
    thread_id=None,
) -> Message:
    """建立一則訊息 / 系統通知(**逐人一列**)。

    參數:
      recipient_sub — 收件人的 `sub`(**不設外鍵**,見 `app/models.py` 的說明)
      sender_sub    — None = 系統發出;人對人時**一律自 token 取**,不取自 request
      source_app    — **一律由 service client 身分推導**,不得取自 request body
      action_url    — 同站值,否則 400(本專案紅線)
    回傳: Message
    副作用: INSERT 一列 `message`
    錯誤: 值不合法 → BadRequest(400),且**不寫入任何東西**

    🔴 驗證全部在 `session.add` **之前**。順序反過來(先加再驗)的話,
       400 的回應旁邊已經留下一列,而下一個查詢就會把它端出來。
    """
    from app.validation import require_choice, require_text, validate_action_url

    safe_subject = require_text(subject, field="subject", max_length=255)
    safe_body = require_text(body, field="body", max_length=20000)
    safe_category = require_choice(category, field="category", allowed=CATEGORIES)
    safe_action_url = validate_action_url(action_url)

    row = Message(
        recipient_sub=recipient_sub,
        sender_sub=sender_sub,
        category=safe_category,
        subject=safe_subject,
        body=safe_body,
        action_url=safe_action_url,
        source_app=source_app,
        thread_id=thread_id,
    )
    session.add(row)
    session.flush()
    return row
