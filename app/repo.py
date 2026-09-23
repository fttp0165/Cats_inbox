# -*- coding: utf-8 -*-
"""資料存取層:本服務**唯一**寫資料庫的地方。

用途: 首登建號、bootstrap 清單比對、角色授與/停用、L1 快取清除(T05);
      訊息讀取與標已讀(T08);公告(T09);訊息的唯一寫入路徑(T10);
      來源登記表與推送去重(T14a)。
副作用: **寫資料庫**。每個函式都標明它寫了什麼。
(⚠ 本行原寫「唯一會寫 `app_user` / `user_role` 的地方」—— T07 起就不只了,T14a 時更正。)

🔴 `display_name` 的**唯一寫入路徑**在本檔的 `ensure_user_on_login()`。
   以 `tests/test_authz.py::test_display_name_only_written_from_own_login_token`
   的 AST 檢查釘住:`app/` 底下其他檔案對 `display_name` 賦值即紅燈。
   理由:契約 §4.2a L1 要求「僅得自本人登入 token 取得」,而多開一條寫入路徑
   **不會有錯誤訊息**,只會讓那句話變成一句話。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.authz import ALL_ROLES, ROLE_ADMIN, ROLE_READER
from app.models import (
    AUDIENCE_ALL,
    AZP_MAX,
    CATEGORIES,
    CATEGORY_SYSTEM,
    SOURCE_LABEL_MAX,
    Announcement,
    AnnouncementRead,
    AppUser,
    Message,
    PushReceipt,
    SourceApp,
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


# ═══════════════════════════════════════════════════════════════════════
# T14a:來源登記表(`azp` → 顯示標籤 + 啟用旗標)
#
# 🔴 我方 2026-08-24 答覆 portal Q2 的落點:「新增來源=管理後台加一列;
#    可停用單一來源而不動程式」。未登記的 `azp` 一律 403(`app/s2s.py`)。
# ═══════════════════════════════════════════════════════════════════════


def get_source_app(session: Session, azp: str) -> SourceApp | None:
    """依 `azp` 取來源;不存在回 None。"""
    return session.get(SourceApp, azp)


def list_source_apps(session: Session) -> list[SourceApp]:
    """列出全部來源(登記先後排序),供後台顯示。"""
    return list(session.scalars(select(SourceApp).order_by(SourceApp.created_at, SourceApp.azp)))


def register_source_app(session: Session, *, azp: str, label: str, created_by: str) -> SourceApp:
    """登記一個來源系統。

    參數:
      azp        — 呼叫方的 client_id(契約 §11 的形態是 `<client_id>-sa`)
      label      — 顯示標籤,以「寄件人」的位置顯示給**每一個收件人**
      created_by — 登記者(管理員的 sub)
    回傳: SourceApp
    副作用: INSERT 一列 `source_app`
    錯誤: 值不合法或已登記 → BadRequest(400),且**不寫入任何東西**

    🔴 **已登記就 400,不默默改標籤。** 標籤是收件人看到的「寄件人」,
       一個會順手覆寫的「登記」按鈕,會讓一次打錯 azp 的操作改掉另一個系統的名字
       —— 而畫面上只是一個成功的 303。
    ⚠ 「不得登記我方自己的 client_id」這條在**後台路由**(它要讀設定),不在這裡。
    """
    from app.validation import BadRequest, require_text

    key = (azp or "").strip()
    if not key or len(key) > AZP_MAX or any(ch.isspace() or ord(ch) < 0x20 for ch in key):
        raise BadRequest("invalid_azp", f"azp 必須是 1–{AZP_MAX} 字、不含空白的 client_id")
    safe_label = require_text(label, field="label", max_length=SOURCE_LABEL_MAX)
    if session.get(SourceApp, key) is not None:
        raise BadRequest("source_already_registered", "這個 azp 已登記;要停用請用停用按鈕")
    row = SourceApp(azp=key, label=safe_label, enabled=True, created_by=created_by,
                    created_at=_utcnow())
    session.add(row)
    session.flush()
    return row


def set_source_app_enabled(session: Session, *, azp: str, enabled: bool) -> bool:
    """啟用/停用一個來源。

    回傳: 是否有找到那一列
    副作用: UPDATE 一列 `source_app`

    🔴 **停用而不刪除**:有推送歷史的來源,它的稽核收據指著它。
    ⚠ 生效是**即時**的 —— `app/s2s.py` 每次請求都查,不快取。
    """
    row = session.get(SourceApp, (azp or "").strip())
    if row is None:
        return False
    row.enabled = enabled
    session.flush()
    return True


# ═══════════════════════════════════════════════════════════════════════
# T14a:推送(`Idempotency-Key` 去重)
#
# 🔴 窗口 24 小時(我方 2026-08-24 答覆 Q3),**寫死**:
#    下限由重試策略決定(§11.6:重試 ≤2 次 + 指數退避,秒到分鐘級),
#    24 小時涵蓋「呼叫方排程整批重跑」這種最長的合理重送。
#    ⚠ **這是推理值,不是實測** —— 上線後若出現「同一事件隔天重複出現」,往上調。
# ═══════════════════════════════════════════════════════════════════════
PUSH_IDEMPOTENCY_WINDOW = timedelta(hours=24)


def _as_utc(value: datetime) -> datetime:
    """把資料庫取回的時間統一成帶 UTC 的 datetime。

    ⚠ naive 只會在 **SQLite** 上出現(它不存時區;PG 的 `timestamptz` 會保留),
      而我方寫入的一律是 UTC —— 與 `app/validation.py::iso_utc` 同一個理由。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def find_push_receipt(session: Session, *, azp: str, idempotency_key: str) -> PushReceipt | None:
    """依 `(azp, key)` 取收據;不存在回 None。

    ⚠ 刻意是模組層函式:測試以它模擬「查的那一刻,並行的另一個請求還沒寫入」
      (`tests/test_push.py::test_concurrent_duplicate_key_is_replayed_not_duplicated`)。
    """
    return session.scalar(
        select(PushReceipt).where(
            PushReceipt.azp == azp, PushReceipt.idempotency_key == idempotency_key
        )
    )


def push_notification(
    session: Session,
    *,
    azp: str,
    source_app: str,
    idempotency_key: str,
    request_hash: str,
    actor_sub: str,
    recipient_sub: str,
    subject: str,
    body: str,
    action_url: str | None,
    now: datetime,
):
    """一次推送:**24h 內同 key 回放、不同內容 422、否則建一則系統通知 + 收據**。

    參數:
      azp / source_app — 來自**已驗證的呼叫方**(`app/s2s.py`),不是 request body
      request_hash     — 請求內容的 SHA-256;同 key 不同 hash → 422
      actor_sub        — `X-User-Id`(已驗過形狀、已正規化)
      now              — 判定窗口用的當下(帶 UTC;測試可注入)
    回傳: (message_id, replayed: bool)
    副作用: 首次 → INSERT `message` + INSERT `push_receipt`;
            超窗 → INSERT `message` + UPDATE 那一列收據;回放 → 無
    錯誤: 內容不合法 → BadRequest(400);同 key 不同內容 → IdempotencyKeyReused(422);
          並行的同 key 已先寫入 → `IntegrityError`(由呼叫端回滾後重試一次 = 回放)

    🔴 **訊息一律經 `create_message()` 建立** —— T10 把 `action_url` 白名單、
       純文字與長度的關卡設在那裡,理由正是「推送 API 動工時,驗證是附帶工作」。
       本函式**只能穿過它**(AST 守門:`app/` 底下只有它構造 `Message`)。
    🔴 **超窗視為新訊息,不拒收**(Q3):拒收會讓一個月後的重跑靜默失敗,
       而「通知沒送到」是本系統最糟的失效模式 —— 寧可重複一次,也不要靜默丟掉。
       超窗時**覆寫**那一列收據指向新訊息(唯一約束只容得下一列;舊的關聯仍在 log)。
    ⚠ 已知且接受:兩個請求**同時**以一把**已過期**的 key 進來時,兩者都會建立新訊息
      (UPDATE 不會撞唯一約束)。後果是重複一則,不是遺失 —— 依 Q3 的取捨接受。
    """
    from app.validation import IdempotencyKeyReused

    receipt = find_push_receipt(session, azp=azp, idempotency_key=idempotency_key)
    if receipt is not None and _as_utc(receipt.created_at) > now - PUSH_IDEMPOTENCY_WINDOW:
        if receipt.request_hash != request_hash:
            raise IdempotencyKeyReused()
        return receipt.message_id, True

    msg = create_message(
        session,
        recipient_sub=recipient_sub,
        subject=subject,
        body=body,
        action_url=action_url,
        source_app=source_app,      # 🔴 來自登記表,不是 body
        sender_sub=None,            # 系統發出(上游 §4.1);觸發者記在收據上,不冒充寄件人
        category=CATEGORY_SYSTEM,
    )
    if receipt is None:
        session.add(PushReceipt(
            azp=azp, idempotency_key=idempotency_key, request_hash=request_hash,
            message_id=msg.id, actor_sub=actor_sub, created_at=now,
        ))
    else:
        receipt.request_hash = request_hash
        receipt.message_id = msg.id
        receipt.actor_sub = actor_sub
        receipt.created_at = now
    session.flush()
    return msg.id, False
